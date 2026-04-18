import logging
from typing import Optional, Dict, Any, Union
from datetime import datetime
import json

logger = logging.getLogger(__name__)
from api.schemas.ingest_schema import (
    FailureIngestResponse,
    JenkinsFailureIngestRequest,
    GithubFailureIngestRequest
)
from api.app.config import settings
from storage.database import database_obj
import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

class PipelineFailureDB:
    """Data-access layer for pipeline failure records stored in PostgreSQL.

    Provides static methods to create, look up, and update failure records
    in the configured ``failures`` table.
    """

    @staticmethod
    def _payload_to_dict(payload: Union[JenkinsFailureIngestRequest, GithubFailureIngestRequest]) -> Dict[str, Any]:
        """Convert request payload to dict for JSONB storage"""
        return payload.model_dump()
    
    @staticmethod
    def _detect_platform(payload: Union[JenkinsFailureIngestRequest, GithubFailureIngestRequest, Dict]) -> str:
        """Detect platform from payload"""
        if isinstance(payload, JenkinsFailureIngestRequest):
            return "jenkins"
        elif isinstance(payload, GithubFailureIngestRequest):
            return "github"
        elif isinstance(payload, dict):
            if "repo" in payload and "owner" in payload:
                return "github"
            elif "job_name" in payload:
                return "jenkins"
        return "jenkins"  # default

    @staticmethod
    def check_if_failure_data_exist(payload: Union[JenkinsFailureIngestRequest, GithubFailureIngestRequest]):
        """Check whether a failure record already exists for the given payload.

        Matches on platform, commit, branch, job name, and build number to
        detect exact duplicates before insertion.

        Args:
            payload: Jenkins or GitHub ingest request payload.

        Returns:
            A dict with ``failure_id``, ``platform``, ``commit``, ``branch``,
            and ``status`` fields if a matching record is found, otherwise
            ``None``.

        Raises:
            psycopg2.DatabaseError: Rolled back automatically on DB error.
        """
        conn = database_obj.get_conn(dbname=settings.POSTGRES_DB)
        try:
            payload_dict = PipelineFailureDB._payload_to_dict(payload)
            platform = PipelineFailureDB._detect_platform(payload)
            
            with conn.cursor() as cursor:
                query = sql.SQL("""
                                SELECT *
                                FROM {}
                                WHERE platform=%s 
                                  AND commit=%s 
                                  AND branch=%s 
                                  AND payload_data->>'job_name'=%s
                                  AND payload_data->>'build_number'=%s                         
                                """).format(sql.Identifier(settings.FAILURE_TABLE))
                
                build_number_str = str(payload_dict.get("build_number", ""))
                job_name = payload_dict.get("job_name")
                
                cursor.execute(query, (
                    platform,
                    payload_dict["commit"],
                    payload_dict["branch"],
                    job_name,
                    build_number_str,
                ))
                records = cursor.fetchone()
                if records:
                    return {
                        "failure_id": records[1],
                        "platform": records[2],
                        "commit": records[3],
                        "branch": records[4],
                        "status": records[5]
                    }
        except (psycopg2.DatabaseError, Exception) as e:
            conn.rollback()
            logger.error("Postgresql check failed: %s", e)
        finally:
            if conn is not None:
                conn.close()

    @staticmethod
    def insert_failure_values(failure_id: str, payload: Union[JenkinsFailureIngestRequest, GithubFailureIngestRequest]):
        """Insert a new failure record into the database with status ``RECEIVED``.

        Args:
            failure_id: UUID string that uniquely identifies this failure.
            payload: Jenkins or GitHub ingest request payload to store as JSONB.

        Raises:
            psycopg2.DatabaseError: Rolled back automatically on DB error.
        """
        conn = database_obj.get_conn(dbname=settings.POSTGRES_DB)
        
        try:
            payload_dict = PipelineFailureDB._payload_to_dict(payload)
            platform = PipelineFailureDB._detect_platform(payload)
            
            with conn.cursor() as cursor:
                query = sql.SQL("""
                    INSERT INTO {} (
                        failure_id,
                        platform,
                        commit,
                        branch,
                        status,
                        payload_data,
                        created_at
                    )
                    VALUES (%s, %s, %s, %s, 'RECEIVED', %s, to_timestamp(%s))
                """).format(sql.Identifier(settings.FAILURE_TABLE))
                
                cursor.execute(query, (
                    failure_id,
                    platform,
                    payload_dict["commit"],
                    payload_dict["branch"],
                    Json(payload_dict),
                    datetime.now().timestamp()
                ))
                conn.commit()
        except (psycopg2.DatabaseError, Exception) as e:
            conn.rollback()
            logger.error("Postgresql insert failed: %s", e)
        finally:
            if conn is not None:
                cursor.close()
                conn.close()
                logger.debug("DB connection closed")

    @staticmethod
    def get_data_by_failure_id(failure_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a failure record by its unique identifier.

        Args:
            failure_id: UUID string of the failure to retrieve.

        Returns:
            A dict with keys ``id``, ``failure_id``, ``platform``, ``commit``,
            ``branch``, ``status``, ``payload_data``, and ``created_at`` when
            the record exists, otherwise ``None``.

        Raises:
            psycopg2.DatabaseError: Rolled back automatically on DB error.
        """
        conn = database_obj.get_conn(dbname=settings.POSTGRES_DB)

        try:
            with conn.cursor() as cursor:
                query = sql.SQL("""
                               SELECT *
                               FROM {}
                               WHERE failure_id=%s  
                               """).format(sql.Identifier(settings.FAILURE_TABLE))
                cursor.execute(query, (failure_id,))
                records = cursor.fetchone()
            
            if not records:
                return None
            
            # records: (id, failure_id, platform, commit, branch, status, payload_data, created_at)
            return {
                "id": records[0],
                "failure_id": records[1],
                "platform": records[2],
                "commit": records[3],
                "branch": records[4],
                "status": records[5],
                "payload_data": records[6],  # JSONB as dict
                "created_at": records[7]
            }
        except (psycopg2.DatabaseError, Exception) as e:
            conn.rollback()
            logger.error("Postgresql query failed: %s", e)
        finally:
            if conn is not None:
                conn.close()
                logger.debug("DB connection closed")

    @staticmethod
    def update_failure_status(failure_id: str, status: str):
        """Update the processing status of an existing failure record.

        Args:
            failure_id: UUID string of the failure to update.
            status: New status string (e.g. ``LOGS_COLLECTED``, ``CLASSIFIED``,
                ``RESOLVED``).

        Raises:
            ValueError: If no failure with *failure_id* exists.
            psycopg2.DatabaseError: Rolled back automatically on DB error.
        """
        conn = database_obj.get_conn(dbname=settings.POSTGRES_DB)
        
        try:
            with conn.cursor() as cursor:
                query = sql.SQL("""
                               UPDATE {}
                               SET status=%s
                               WHERE failure_id=%s  
                               """).format(sql.Identifier(settings.FAILURE_TABLE))
                cursor.execute(query,(
                            status,failure_id
                             ))
                if cursor.rowcount == 0:
                    raise ValueError(f"No failure found with id {failure_id}")
                conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Postgres update failed: %s", e)
        finally:
            if conn is not None:
                conn.close()
                logger.debug("DB connection closed")



pipeline_failure_retriever = PipelineFailureDB()