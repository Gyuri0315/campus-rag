"""Supabase RPC search wrapper."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from supabase import Client, create_client

logger = logging.getLogger(__name__)


def build_supabase_client(url: str, key: str) -> Client:
    return create_client(url, key)


def _vector_literal(vec: List[float]) -> str:
    """pgvector accepts text literal '[v1,v2,...]'.

    PostgREST sometimes fails to coerce a JSON array into `extensions.vector`
    when the RPC parameter has that exact type, so we send the canonical
    text form which the database casts reliably.
    """
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


def search(
    client: Client,
    *,
    rpc_names: List[str],
    embedding: List[float],
    top_k: int,
    min_similarity: float,
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Fan out across RPCs, merge rows, sort by similarity desc, trim to top_k.

    Each RPC must accept the same (query_embedding, match_count, min_similarity,
    metadata_filter) signature and return rows with a `similarity` field. RPCs
    apply min_similarity in-DB, so the merged set is already filtered. A failure
    in one RPC is logged at WARN and skipped — partial results still flow.
    """
    payload = {
        "query_embedding": _vector_literal(embedding),
        "match_count": top_k,
        "min_similarity": min_similarity,
        "metadata_filter": metadata_filter or {},
    }

    merged: List[Dict[str, Any]] = []
    for rpc_name in rpc_names:
        try:
            response = client.rpc(rpc_name, payload).execute()
        except Exception:
            logger.warning("retrieval: rpc=%s failed, skipping", rpc_name, exc_info=True)
            continue
        rows = response.data or []
        logger.info("retrieval: rpc=%s rows=%d", rpc_name, len(rows))
        merged.extend(rows)

    merged.sort(key=lambda r: float(r.get("similarity") or 0.0), reverse=True)
    trimmed = merged[:top_k]
    logger.info(
        "retrieval: merged=%d trimmed=%d top_sim=%.3f",
        len(merged),
        len(trimmed),
        float(trimmed[0].get("similarity") or 0.0) if trimmed else 0.0,
    )
    return trimmed
