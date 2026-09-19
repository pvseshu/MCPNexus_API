"""Delete all rows from every table in the Postgres database, keeping the table definitions.

Standalone: reads connection settings from .env (POSTGRES_*), no Django needed.
Requires: psycopg[binary], python-dotenv.

Usage:
    python scripts/clear_postgres_data.py            # asks for confirmation
    python scripts/clear_postgres_data.py --yes      # no prompt
    python scripts/clear_postgres_data.py --include-system   # also wipe django_migrations etc.

By default django_migrations, django_content_type and auth_permission are kept, since
emptying them makes Django think migrations never ran / breaks permissions until re-migrate.
"""
import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg import sql

SYSTEM_TABLES = {"django_migrations", "django_content_type", "auth_permission"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--include-system", action="store_true", help="also clear Django bookkeeping tables")
    parser.add_argument("--schema", default="public", help="schema to clear (default: public)")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    conn_info = dict(
        dbname=os.getenv("POSTGRES_DB", "mcpnexus"),
        user=os.getenv("POSTGRES_USER", "mcpnexus"),
        password=os.getenv("POSTGRES_PASSWORD", "mcpnexus"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
    )

    with psycopg.connect(**conn_info) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (args.schema,)
        ).fetchall()
        tables = [r[0] for r in rows if args.include_system or r[0] not in SYSTEM_TABLES]
        if not tables:
            print("No tables to clear.")
            return 0

        print(f"Database: {conn_info['dbname']} @ {conn_info['host']}:{conn_info['port']}")
        print("Will delete ALL data from:")
        for t in tables:
            print(f"  - {args.schema}.{t}")
        if not args.yes and input("Type 'yes' to continue: ").strip().lower() != "yes":
            print("Aborted.")
            return 1

        # One TRUNCATE handles FK dependencies; RESTART IDENTITY resets serial/identity counters.
        names = sql.SQL(", ").join(sql.Identifier(args.schema, t) for t in tables)
        conn.execute(sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(names))
        conn.commit()
    print(f"Done. Cleared {len(tables)} tables.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
