"""Apply SQL migrations in order, once each.

Each file in db/migrations/ named NNN_description.sql is one migration. Applied versions are recorded
in the schema_migrations table, so running this again only applies new files. A migration runs in a
single transaction: if any statement fails, none of that file's changes are kept.

Usage:
    python -m bearcast.db.migrate            # apply anything new
    python -m bearcast.db.migrate --status   # list applied / pending
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from sqlalchemy import Engine, text

from bearcast.db import engine

log = logging.getLogger(__name__)
MIGRATIONS = Path("db/migrations")
NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def migration_files(folder: Path = MIGRATIONS) -> list[Path]:
    files = sorted(p for p in folder.glob("*.sql"))
    bad = [p.name for p in files if not NAME.match(p.name)]
    if bad:
        raise ValueError(f"migration files must look like 001_description.sql: {bad}")
    versions = [p.name[:3] for p in files]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration numbers: {versions}")
    return files


def applied(eng: Engine) -> set[str]:
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations ("
                          "version text PRIMARY KEY, name text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"))
        return {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}


def migrate(eng: Engine, folder: Path = MIGRATIONS) -> list[str]:
    done = applied(eng)
    new = []
    for f in migration_files(folder):
        version = f.name[:3]
        if version in done:
            continue
        with eng.begin() as conn:                       # one transaction per file
            # raw DB-API cursor with no parameters, so '%' in SQL comments isn't read as a placeholder
            conn.connection.cursor().execute(f.read_text())
            conn.execute(text("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)"),
                         {"v": version, "n": f.name})
        log.info("applied %s", f.name)
        new.append(f.name)
    return new


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--status", action="store_true")
    p.add_argument("--url-var", default="DATABASE_URL", help="environment variable holding the database URL")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    eng = engine(args.url_var)
    if args.status:
        done = applied(eng)
        for f in migration_files():
            log.info("%-8s %s", "applied" if f.name[:3] in done else "PENDING", f.name)
        return 0
    new = migrate(eng)
    if new:
        log.info("%d new migration(s) applied", len(new))
    else:
        log.info("database is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())