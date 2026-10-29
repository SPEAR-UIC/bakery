"""bakery/database.py - open, version and migrate the bakery database

URL: the one given, else BAKERY_DATABASE_URL, else ./bakery.sqlite.
SQLite by default; PostgreSQL or MySQL by URL.
A database from a later schema version is refused; older ones are migrated.

Authors: Melanie Cornelius, Claude Opus 4.8, Claude Opus 5.5, Qwen 27B Flux
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import NoSuchModuleError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from models import SCHEMA_VERSION, Base, Meta, SourceFile

URL_VARIABLE = "BAKERY_DATABASE_URL"

DEFAULT_FILENAME = "bakery.sqlite"

# A missing driver's own error names a module, not what to install.
SUGGESTED_DRIVERS = {
    "postgresql": "pip install 'psycopg[binary]'   (url: postgresql+psycopg://...)",
    "mysql": "pip install pymysql                  (url: mysql+pymysql://...)",
    "mariadb": "pip install pymysql                (url: mariadb+pymysql://...)",
}


class DatabaseVersionMismatch(RuntimeError):
    """Raised for a later schema version or a failed migration."""

    pass


# Each version from the one before. Additive only: a half-upgraded database still works.
MIGRATIONS = {
    2: ["ALTER TABLE field_docs ADD COLUMN note TEXT"],
    3: ["ALTER TABLE enumerations ADD COLUMN code_description TEXT",
        "ALTER TABLE enumerations ADD COLUMN meaning_description TEXT"],
    4: ["ALTER TABLE source_files ADD COLUMN rows_are_whole_file BOOLEAN"],
}


def resolve_url(url=None, directory=None):
    """Return url, else BAKERY_DATABASE_URL, else a SQLite file in directory.
    A MySQL URL gets charset utf8mb4 unless it names one."""
    url = url or os.environ.get(URL_VARIABLE)
    if not url:
        return f"sqlite:///{Path(directory or Path.cwd()) / DEFAULT_FILENAME}"
    parsed = make_url(url)
    if parsed.get_backend_name() in ("mysql", "mariadb") and "charset" not in parsed.query:
        url = parsed.update_query_dict({"charset": "utf8mb4"}).render_as_string(hide_password=False)
    return url


def describe(url):
    """Return a short description of the database, password hidden."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return f"SQLite at {parsed.database or ':memory:'}"
    return parsed.render_as_string(hide_password=True)


def create_engine_for(url, echo=False):
    """Return an engine; exit with an install hint if the driver is missing."""
    try:
        engine = create_engine(url, echo=echo, future=True)
    except NoSuchModuleError as problem:
        backend = make_url(url).get_backend_name()
        hint = SUGGESTED_DRIVERS.get(backend)
        raise SystemExit(
            f"Cannot open {describe(url)}: {problem}\n"
            + (f"The driver is not installed. Try:\n    {hint}" if hint else
               "That database backend needs a driver installed.")
        ) from problem

    # SQLite leaves foreign keys off unless asked, so ondelete="CASCADE" would do nothing.
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enforce_foreign_keys(connection, _record):
            """Turn on foreign keys for each new SQLite connection."""
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


class Database:
    """One database: opening, versioning and closing."""

    def __init__(self, url=None, directory=None, echo=False, create=True):
        """Open the engine; create or migrate the schema if create."""
        self.url = resolve_url(url, directory)
        self.engine = create_engine_for(self.url, echo=echo)
        self.new_session = sessionmaker(bind=self.engine, future=True)
        self.created = False
        self.migrated_from = None
        if create:
            self.prepare()

    @property
    def is_sqlite(self):
        """Return whether this is a SQLite database."""
        return self.engine.dialect.name == "sqlite"

    def prepare(self):
        """Create missing tables; record, check or migrate the schema version."""
        # create_all adds missing tables but never missing columns, hence MIGRATIONS.
        Base.metadata.create_all(self.engine)

        with self.new_session() as session:
            recorded = session.get(Meta, "schema_version")
            if recorded is None:
                session.add(Meta(name="schema_version", value=str(SCHEMA_VERSION)))
                session.commit()
                self.created = True
                return
            # FIXME: a corrupt row raises ValueError, not DatabaseVersionMismatch.
            found = int(recorded.value)
            if found > SCHEMA_VERSION:
                # SQLAlchemy would not see a later version's columns; answers would seem lost.
                raise DatabaseVersionMismatch(
                    f"{describe(self.url)} was written for schema version "
                    f"{found}, and this is version {SCHEMA_VERSION}. Refusing to "
                    "read a database from a later version rather than guess at "
                    "what changed in it."
                )
            if found < SCHEMA_VERSION:
                self.migrate_from(found)

    def migrate_from(self, found):
        """Apply each migration after found, recording the version after each step."""
        # One transaction per step, so an interrupted upgrade resumes.
        # A column already there is the wanted state, not an error.
        for version in range(found + 1, SCHEMA_VERSION + 1):
            for statement in MIGRATIONS.get(version, []):
                try:
                    with self.engine.begin() as connection:
                        connection.execute(text(statement))
                except (OperationalError, ProgrammingError) as problem:
                    if "duplicate column" not in str(problem).lower() and \
                            "already exists" not in str(problem).lower():
                        raise DatabaseVersionMismatch(
                            f"Could not bring {describe(self.url)} from version "
                            f"{found} to {SCHEMA_VERSION}: {problem}\n"
                            f"The step that failed was: {statement}"
                        ) from problem
            with self.new_session() as session:
                session.get(Meta, "schema_version").value = str(version)
                session.commit()
        self.migrated_from = found

    def session(self) -> Session:
        """Return a new session."""
        return self.new_session()

    def is_empty(self):
        """Return whether no source file has been read in."""
        with self.new_session() as session:
            return session.scalar(select(SourceFile.id).limit(1)) is None

    def close(self):
        """Dispose of the engine."""
        self.engine.dispose()

    def __enter__(self):
        """Return self."""
        return self

    def __exit__(self, *exception):
        """Close the database."""
        self.close()

    def __repr__(self):
        """Return Database(<description>)."""
        return f"Database({describe(self.url)})"
