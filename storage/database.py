import logging
import time

import psycopg2
from psycopg2 import sql
from api.app.config import settings

logger = logging.getLogger(__name__)


class DatabaseInit:
    """Handles PostgreSQL database bootstrap and schema initialisation."""

    @staticmethod
    def get_conn(dbname: str = None) -> psycopg2.extensions.connection:
        """
        Obtain a psycopg2 connection with up to three retry attempts.

        Args:
            dbname: Name of the database to connect to.  Defaults to
                    ``settings.POSTGRES_DB`` when *None*.

        Returns:
            An open psycopg2 connection object.

        Raises:
            RuntimeError: If all three connection attempts fail.
        """
        for attempt in range(3):
            try:
                conn = psycopg2.connect(
                    database=dbname,
                    user=settings.POSTGRES_USER,
                    password=settings.POSTGRES_PASSWORD,
                    port=settings.DB_PORT,
                    host=settings.DB_HOST,
                )
                return conn
            except psycopg2.OperationalError as exc:
                logger.warning(
                    "[DB RETRY %d/3] waiting for postgres — %s", attempt + 1, exc
                )
                time.sleep(2)
        raise RuntimeError("Database connection failed after 3 attempts.")

    @staticmethod
    def init_db() -> bool:
        """
        Create the application database if it does not already exist.

        Connects to the default ``postgres`` maintenance database, checks for
        the configured application DB and creates it when absent.

        Returns:
            ``True`` on success, ``False`` when a database error occurs.
        """
        conn = DatabaseInit.get_conn(dbname="postgres")
        conn.autocommit = True
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    [settings.POSTGRES_DB],
                )
                if not cursor.fetchone():
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {}")
                        .format(sql.Identifier(settings.POSTGRES_DB))
                    )
                    logger.info(
                        "Database '%s' created successfully.", settings.POSTGRES_DB
                    )
            return True
        except psycopg2.Error as exc:
            logger.error("DB init failed: %s", exc)
            return False
        finally:
            conn.close()

    @staticmethod
    def ensureFailureMetadataTable() -> None:
        """
        Create the failure metadata table if it does not already exist.

        The table records every ingest request with its processing status,
        CI platform, commit, branch and the original JSON payload.
        """
        conn = DatabaseInit.get_conn(dbname=settings.POSTGRES_DB)
        try:
            query = sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    id           SERIAL PRIMARY KEY,
                    failure_id   VARCHAR(64)  NOT NULL UNIQUE,
                    platform     VARCHAR(50)  NOT NULL DEFAULT 'jenkins',
                    commit       VARCHAR(255) NOT NULL,
                    branch       VARCHAR(255) NOT NULL,
                    status       VARCHAR(50)  NOT NULL DEFAULT 'RECEIVED',
                    payload_data JSONB        NOT NULL,
                    created_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                );
            """).format(sql.Identifier(settings.FAILURE_TABLE))
            with conn.cursor() as cursor:
                cursor.execute(query)
                conn.commit()
            logger.debug("Failure metadata table ready.")
        except psycopg2.DatabaseError as exc:
            conn.rollback()
            logger.error("Failed to create failure metadata table: %s", exc)
        finally:
            conn.close()

    @staticmethod
    def ensureFailurePatternTable() -> None:
        """
        Create the failure knowledge / pattern table if it does not already exist.

        The table stores RCA results alongside a pgvector embedding so that
        future similar failures can be resolved from the cache without calling
        the LLM again.
        """
        conn = DatabaseInit.get_conn(dbname=settings.POSTGRES_DB)
        try:
            query = sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    id                    SERIAL PRIMARY KEY,
                    validated_category    VARCHAR(100),
                    root_cause            TEXT,
                    error_line            TEXT         NOT NULL,
                    owner_team            VARCHAR(100),
                    remediation           JSONB,
                    secondary_remediations JSONB,
                    confidence            FLOAT,
                    severity              VARCHAR(50),
                    recurrence_count      INT          DEFAULT 1,
                    analyzed_at           TIMESTAMP,
                    evidence_url          TEXT,
                    embedding             VECTOR(1024) NOT NULL,
                    fingerprint           VARCHAR(64)  UNIQUE NOT NULL
                );
            """).format(sql.Identifier(settings.FAILURE_PATTERN_TABLE))
            with conn.cursor() as cursor:
                cursor.execute(query)
                conn.commit()
            logger.debug("Failure pattern table ready.")
        except psycopg2.DatabaseError as exc:
            conn.rollback()
            logger.error("Failed to create failure pattern table: %s", exc)
        finally:
            conn.close()


database_obj = DatabaseInit()