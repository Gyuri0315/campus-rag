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
    rpc_name: str,
    embedding: List[float],
    top_k: int,
    min_similarity: float,
    metadata_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Call the `match_rag_documents` (or compatible) RPC and return rows."""

    payload = {
        "query_embedding": _vector_literal(embedding),
        "match_count": top_k,
        "min_similarity": min_similarity,
        "metadata_filter": metadata_filter or {},
    }
    response = client.rpc(rpc_name, payload).execute()
    rows = response.data or []
    logger.info("retrieval: rpc=%s rows=%d", rpc_name, len(rows))
    return rows
