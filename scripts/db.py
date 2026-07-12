"""Shared PostgreSQL connection helpers for local pipeline scripts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def with_connect_timeout(conninfo: str, timeout_seconds: int = 10) -> str:
    parts = urlsplit(conninfo)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("connect_timeout", str(timeout_seconds))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def load_database_conninfo() -> tuple[str | None, str | None]:
    """Return a PostgreSQL connection string and the env var it came from."""

    load_dotenv(PROJECT_ROOT / "backend" / ".env")
    env = {key.upper(): value for key, value in os.environ.items()}
    for name in (
        "DATABASE_URL",
        "SUPABASE_POOLER_URL",
        "SUPABASE_DATABASE_URL",
        "SUPABASE_DB_URL",
        "POSTGRES_URL",
    ):
        value = env.get(name)
        if not value:
            continue
        scheme = urlsplit(value).scheme.lower()
        if scheme in {"postgres", "postgresql"}:
            return value, name
        if name == "SUPABASE_DB_URL" and scheme in {"http", "https"}:
            raise RuntimeError(
                "SUPABASE_DB_URL contains a Supabase API URL, not a PostgreSQL "
                "connection string. Keep SUPABASE_URL for the API URL and set "
                "DATABASE_URL or SUPABASE_POOLER_URL to a postgresql:// connection string."
            )
    return None, None


def connect_postgres(
    *,
    row_factory: Any = None,
    prepare_threshold: int | None = None,
    autocommit: bool | None = None,
    connect_timeout_seconds: int = 10,
):
    """Connect using backend/.env DATABASE_URL/pooler settings or PG* variables."""

    import psycopg

    conninfo, _conninfo_env = load_database_conninfo()
    connect_kwargs: dict[str, Any] = {
        "prepare_threshold": prepare_threshold,
    }
    if row_factory is not None:
        connect_kwargs["row_factory"] = row_factory
    if autocommit is not None:
        connect_kwargs["autocommit"] = autocommit

    if conninfo:
        try:
            return psycopg.connect(
                with_connect_timeout(conninfo, connect_timeout_seconds),
                **connect_kwargs,
            )
        except psycopg.OperationalError as exc:
            message = str(exc)
            if (
                ("Permission denied" in message or "getaddrinfo failed" in message)
                and "db." in conninfo
                and ".supabase.co" in conninfo
            ):
                raise RuntimeError(
                    "Could not connect to Supabase PostgreSQL. If this is a direct "
                    "db.<project-ref>.supabase.co URL, use the Supabase Shared Pooler "
                    "connection string instead; direct connections may require IPv6 access. "
                    "Set it as DATABASE_URL or SUPABASE_POOLER_URL in backend/.env."
                ) from exc
            raise

    required = ["PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing database configuration. Set DATABASE_URL, SUPABASE_POOLER_URL, "
            f"SUPABASE_DATABASE_URL, or set {', '.join(required)}."
        )

    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.getenv("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        sslmode=os.getenv("PGSSLMODE", "require"),
        connect_timeout=connect_timeout_seconds,
        **connect_kwargs,
    )
