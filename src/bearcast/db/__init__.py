"""Database helpers: connection settings come from the DATABASE_URL in .env (never hard-coded)."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import Engine, create_engine

load_dotenv(find_dotenv(usecwd=True))   # .env in the folder you run commands from (the repo root)
load_dotenv()                           # fallback: search upward from this package (never overrides)


def database_url(var: str = "DATABASE_URL") -> str:
    url = os.environ.get(var)
    if not url:
        raise RuntimeError(f"{var} is not set. Copy .env.example to .env and fill it in.")
    # hosted databases (Neon, Render, ...) hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver named
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


@lru_cache(maxsize=None)
def engine(var: str = "DATABASE_URL") -> Engine:
    return create_engine(database_url(var), pool_pre_ping=True)
