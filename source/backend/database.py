"""SQLAlchemy connection factory for the app's MySQL and PostgreSQL databases."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


_POSTGRES_SCHEMAS = {
    "DATABASE_URL": ("DEVELOPMENT_DATABASE_SCHEMA", "fieldnotes_dev"),
    "STAGING_DATABASE_URL": ("STAGING_DATABASE_SCHEMA", "fieldnotes_stage"),
    "PRODUCTION_DATABASE_URL": ("PRODUCTION_DATABASE_SCHEMA", "fieldnotes_prod"),
}


def _database_schema_for_target(environment_variable: str) -> str:
    setting, default = _POSTGRES_SCHEMAS.get(
        environment_variable,
        ("DATABASE_SCHEMA", "fieldnotes_dev"),
    )
    schema = os.environ.get(setting, default).strip()
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema) or schema == "public":
        raise RuntimeError(
            f"{setting} must be a non-public PostgreSQL schema name."
        )
    return schema


def _database_connection_config(
    database_url: str | None = None,
    *,
    environment_variable: str = "DATABASE_URL",
) -> tuple[str, dict[str, object]]:
    """Normalize a protected database URL and return driver connection options."""
    database_url = database_url or os.environ.get(environment_variable)
    if not database_url:
        raise RuntimeError(f"{environment_variable} is not configured.")

    parsed_url = urlsplit(database_url)
    scheme = parsed_url.scheme.lower()
    query = parse_qsl(parsed_url.query, keep_blank_values=True)
    connect_args: dict[str, object] = {}
    sql_url = make_url(database_url)
    host = (sql_url.host or "").lower()
    port = sql_url.port

    if scheme == "mysql":
        scheme = "mysql+pymysql"
    elif scheme == "postgres":
        scheme = "postgresql+psycopg2"
    elif scheme == "postgresql":
        scheme = "postgresql+psycopg2"
    elif scheme not in {"mysql+pymysql", "postgresql+psycopg2"}:
        raise RuntimeError(
            f"{environment_variable} must be a supported MySQL or PostgreSQL connection string."
        )

    if scheme == "postgresql+psycopg2" and host.endswith(".pooler.supabase.com"):
        if host.startswith("@"):
            raise RuntimeError(
                f"{environment_variable} has a malformed Supabase pooler hostname. "
                "Copy the full Session Pooler URI without editing it."
            )
        if port not in (None, 5432):
            raise RuntimeError(
                f"{environment_variable} must use the Supabase Session Pooler "
                "on port 5432; port 6543 is the transaction pooler."
            )

    ssl_mode = next(
        (value.lower() for key, value in query if key.lower() == "ssl-mode"),
        None,
    )
    if ssl_mode:
        query = [(key, value) for key, value in query if key.lower() != "ssl-mode"]
        if scheme == "mysql+pymysql":
            # Aiven commonly uses ssl-mode=REQUIRED; PyMySQL expects this as
            # a connect argument rather than a URL query parameter.
            if ssl_mode == "disabled":
                connect_args["ssl_disabled"] = True
            elif ssl_mode in {"required", "verify_ca", "verify_identity"}:
                connect_args["ssl"] = {}
            else:
                raise RuntimeError(
                    f"Unsupported MySQL ssl-mode in {environment_variable}."
                )
        else:
            postgres_ssl_modes = {
                "disabled": "disable",
                "allow": "allow",
                "prefer": "prefer",
                "required": "require",
                "verify_ca": "verify-ca",
                "verify_identity": "verify-full",
            }
            postgres_ssl_mode = postgres_ssl_modes.get(ssl_mode)
            if not postgres_ssl_mode:
                raise RuntimeError(
                    f"Unsupported PostgreSQL ssl-mode in {environment_variable}."
                )
            if not any(key.lower() == "sslmode" for key, _ in query):
                query.append(("sslmode", postgres_ssl_mode))

    if (
        scheme == "postgresql+psycopg2"
        and host.endswith(".pooler.supabase.com")
        and not any(key.lower() == "sslmode" for key, _ in query)
    ):
        query.append(("sslmode", "require"))

    parsed_url = parsed_url._replace(scheme=scheme)
    parsed_url = parsed_url._replace(query=urlencode(query))
    return urlunsplit(parsed_url), connect_args


def create_database_engine(
    database_url: str | None = None,
    *,
    schema: str | None = None,
) -> Engine:
    """Create a pooled engine using only the URL for the active runtime."""
    environment_variable = "DATABASE_URL"
    if database_url is None:
        runtime = os.environ.get("NODE_ENV", "development").strip().lower()
        if runtime == "production":
            environment_variable = "PRODUCTION_DATABASE_URL"
        elif runtime in {"development", "test"}:
            target = os.environ.get("DATABASE_TARGET", "development").strip().lower()
            if target == "staging" and runtime == "development":
                environment_variable = "STAGING_DATABASE_URL"
            elif target != "development":
                raise RuntimeError(
                    "DATABASE_TARGET must be development or staging, and staging "
                    "is available only when NODE_ENV=development."
                )
        else:
            raise RuntimeError(
                "NODE_ENV must be development, test, or production before "
                "opening the application database."
            )

        database_url = os.environ.get(environment_variable)
        if not database_url:
            raise RuntimeError(f"{environment_variable} is not configured.")

    database_url, connect_args = _database_connection_config(
        database_url,
        environment_variable=environment_variable,
    )
    normalized_url = make_url(database_url)
    if normalized_url.drivername.startswith("postgresql"):
        schema = schema or _database_schema_for_target(environment_variable)
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema) or schema == "public":
            raise RuntimeError("PostgreSQL schema must be a non-public schema name.")
        existing_options = normalized_url.query.get("options", "")
        if isinstance(existing_options, tuple):
            existing_options = " ".join(existing_options)
        database_url = normalized_url.difference_update_query(["options"]).render_as_string(
            hide_password=False
        )
        connect_args["options"] = f"-c search_path={schema},public"
        if existing_options:
            connect_args["options"] = f"{existing_options} {connect_args['options']}"
    elif schema is not None:
        raise RuntimeError("Explicit schemas are supported only for PostgreSQL.")

    if environment_variable == "STAGING_DATABASE_URL":
        production_url = os.environ.get("PRODUCTION_DATABASE_URL")
        if production_url:
            production_url, _ = _database_connection_config(
                production_url,
                environment_variable="PRODUCTION_DATABASE_URL",
            )
            staging_target = make_url(database_url)
            production_target = make_url(production_url)
            if not all(
                (
                    staging_target.host,
                    staging_target.database,
                    production_target.host,
                    production_target.database,
                )
            ):
                raise RuntimeError(
                    "Staging and production URLs must identify a database "
                    "host and database."
                )
            same_database = (
                staging_target.drivername,
                staging_target.host.lower(),
                staging_target.port or (5432 if staging_target.drivername.startswith("postgresql") else 3306),
                staging_target.database,
            ) == (
                production_target.drivername,
                production_target.host.lower(),
                production_target.port or (5432 if production_target.drivername.startswith("postgresql") else 3306),
                production_target.database,
            )
            if same_database and (
                not staging_target.drivername.startswith("postgresql")
                or _database_schema_for_target("STAGING_DATABASE_URL")
                == _database_schema_for_target("PRODUCTION_DATABASE_URL")
            ):
                raise RuntimeError(
                    "STAGING_DATABASE_URL and PRODUCTION_DATABASE_URL must "
                    "target distinct databases or distinct PostgreSQL schemas."
                )

    return create_engine(
        database_url,
        connect_args=connect_args,
        # Supabase pooler connections can be recycled independently of the
        # application, so check them on checkout. Preserve the lower-overhead
        # behavior for the existing MySQL service.
        pool_pre_ping=normalized_url.drivername.startswith("postgresql"),
        pool_recycle=1800,
        future=True,
    )


@contextmanager
def isolated_mysql_test_engine() -> Iterator[Engine]:
    """Create a uniquely named disposable schema from MYSQL_TEST_DATABASE_URL."""
    if os.environ.get("ENVIRONMENT", "").lower() in {"production", "prod"}:
        raise RuntimeError("MySQL integration tests cannot run in production.")

    configured_url = os.environ.get("MYSQL_TEST_DATABASE_URL")
    if not configured_url:
        raise RuntimeError(
            "MYSQL_TEST_DATABASE_URL is required for MySQL integration tests."
        )

    normalized_url, connect_args = _database_connection_config(
        configured_url,
        environment_variable="MYSQL_TEST_DATABASE_URL",
    )
    test_url = make_url(normalized_url)
    if test_url.drivername != "mysql+pymysql":
        raise RuntimeError("MYSQL_TEST_DATABASE_URL must be a MySQL connection string.")
    configured_database = test_url.database or ""
    database_label = configured_database.lower()
    if "test" not in database_label or re.search(
        r"(^|[_-])(prod|production)([_-]|$)", database_label
    ):
        raise RuntimeError(
            "MYSQL_TEST_DATABASE_URL must name a test-only database prefix."
        )

    app_url = os.environ.get("DATABASE_URL")
    if app_url:
        normalized_app_url, _ = _database_connection_config(app_url)
        if make_url(normalized_app_url).database == configured_database:
            raise RuntimeError(
                "MYSQL_TEST_DATABASE_URL must not target the application database."
            )

    suffix = os.urandom(8).hex()
    safe_prefix = re.sub(r"[^a-zA-Z0-9_]", "_", configured_database)[:40]
    schema_name = f"{safe_prefix}_{suffix}"
    server_url = test_url.set(database=None)
    admin_engine = create_engine(
        server_url,
        connect_args=connect_args,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        future=True,
    )
    engine: Engine | None = None
    quoted_schema = admin_engine.dialect.identifier_preparer.quote(schema_name)
    try:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE {quoted_schema} "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
        isolated_url = test_url.set(database=schema_name).render_as_string(
            hide_password=False
        )
        engine = create_database_engine(isolated_url)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_schema}"))
        admin_engine.dispose()


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a SQLAlchemy session factory for the application database."""
    return sessionmaker(
        bind=engine or create_database_engine(),
        autoflush=True,
        expire_on_commit=False,
        future=True,
    )


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
) -> Iterator[Session]:
    """Yield a session and commit or roll back one complete unit of work."""
    session = (session_factory or create_session_factory())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def verify_database_connection() -> None:
    """Run a harmless ping query and raise a safe, non-sensitive error on failure."""
    engine = create_database_engine()
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, TypeError, ValueError) as error:
        raise RuntimeError("The database connection could not be verified.") from error
    finally:
        engine.dispose()