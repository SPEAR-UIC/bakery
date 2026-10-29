#!/usr/bin/env python3
"""bakery/migrate_database.py - copy every row of one bakery database into another

    python3 migrate_database.py sqlite:///bakery.sqlite "mysql+pymysql://bakery@localhost/bakery"
The target must be empty. Row ids are kept, so every link survives.
The source is migrated to the current schema version first; row counts are checked after.
The password comes from the URL or BAKERY_DATABASE_URL; it is never printed.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import argparse
import os

from sqlalchemy import func, insert, select, text

from database import URL_VARIABLE, Database, describe
from models import Base, Meta

BATCH_ROWS = 1000


def row_counts(engine):
    """Return {table name: row count} for every bakery table."""
    with engine.connect() as connection:
        return {table.name: connection.scalar(select(func.count()).select_from(table))
                for table in Base.metadata.sorted_tables}


def copy_rows(source, target):
    """Copy every table but the schema version, parents first, in batches."""
    with source.engine.connect() as reading, target.engine.begin() as writing:
        if target.engine.dialect.name == "mysql":
            # machine_iterations refers to itself; rows need not arrive in id order.
            writing.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in Base.metadata.sorted_tables:
            if table.name == Meta.__tablename__:
                continue
            rows = reading.execute(select(table).order_by(*table.primary_key.columns))
            while batch := rows.mappings().fetchmany(BATCH_ROWS):
                writing.execute(insert(table), [dict(row) for row in batch])
        if target.engine.dialect.name == "mysql":
            writing.execute(text("SET FOREIGN_KEY_CHECKS=1"))


def migrate(source_url, target_url):
    """Copy source into an empty target; return {table: count}. Raise SystemExit on a problem."""
    source = Database(source_url)              # migrates the source to the current version
    target = Database(target_url)
    if any(count for name, count in row_counts(target.engine).items()
           if name != Meta.__tablename__):
        raise SystemExit(f"{describe(target.url)} already holds data; refusing to copy into it.")
    copy_rows(source, target)
    before, after = row_counts(source.engine), row_counts(target.engine)
    if before != after:
        differ = {name: (before[name], after[name]) for name in before if before[name] != after[name]}
        raise SystemExit(f"Row counts differ after copying (source, target): {differ}")
    source.close()
    target.close()
    return after


def main():
    """Parse arguments, copy, and print each table's row count."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="the database to copy from, e.g. sqlite:///bakery.sqlite")
    parser.add_argument("target", nargs="?", default=os.environ.get(URL_VARIABLE),
                        help=f"the empty database to copy into (default: ${URL_VARIABLE})")
    arguments = parser.parse_args()
    if not arguments.target:
        parser.error(f"give a target URL or set {URL_VARIABLE}")
    counts = migrate(arguments.source, arguments.target)
    print(f"Copied {describe(arguments.source)} into {describe(arguments.target)}:")
    for name, count in counts.items():
        print(f"  {name:22} {count:6,}")


if __name__ == "__main__":
    main()
