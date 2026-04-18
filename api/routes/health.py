from fastapi import APIRouter
import time
import psycopg2
import redis
from api.app.config import settings
from workers.celery_app import celery_app

router = APIRouter()

def check_postgres() -> dict:
    """Check PostgreSQL availability and latency"""
    start = time.time()
    try:
        conn = psycopg2.connect(
            database=settings.POSTGRES_DB,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            port=settings.DB_PORT,
            host=settings.DB_HOST,
            connect_timeout=5
        )
        latency = (time.time() - start) * 1000  # Convert to ms
        conn.close()
        return {
            "status": "ok",
            "latency_ms": round(latency, 2)
        }
    except Exception as e:
        latency = (time.time() - start) * 1000
        return {
            "status": "error",
            "error": str(e),
            "latency_ms": round(latency, 2)
        }

def check_redis() -> dict:
    """Check Redis availability and latency"""
    start = time.time()
    try:
        r = redis.Redis(
            host=settings.REDIS_HOST,
            port=int(settings.REDIS_PORT),
            db=0,
            socket_connect_timeout=5,
            socket_keepalive=True
        )
        r.ping()
        latency = (time.time() - start) * 1000  # Convert to ms
        return {
            "status": "ok",
            "latency_ms": round(latency, 2)
        }
    except Exception as e:
        latency = (time.time() - start) * 1000
        return {
            "status": "error",
            "error": str(e),
            "latency_ms": round(latency, 2)
        }

def check_celery() -> dict:
    """Check Celery worker availability and latency"""
    start = time.time()
    try:
        inspect = celery_app.control.inspect()
        active_workers = inspect.active()
        
        if active_workers is None or len(active_workers) == 0:
            latency = (time.time() - start) * 1000
            return {
                "status": "warning",
                "message": "No active workers",
                "latency_ms": round(latency, 2),
                "workers": 0
            }
        
        latency = (time.time() - start) * 1000
        return {
            "status": "ok",
            "latency_ms": round(latency, 2),
            "workers": len(active_workers),
            "worker_names": list(active_workers.keys())
        }
    except Exception as e:
        latency = (time.time() - start) * 1000
        return {
            "status": "error",
            "error": str(e),
            "latency_ms": round(latency, 2)
        }

@router.get('/health')
async def health():
    postgres_check = check_postgres()
    redis_check = check_redis()
    celery_check = check_celery()
    
    overall_status = "healthy"
    if any(check["status"] == "error" for check in [postgres_check, redis_check, celery_check]):
        overall_status = "unhealthy"
    elif any(check.get("status") == "warning" for check in [postgres_check, redis_check, celery_check]):
        overall_status = "degraded"
    
    return {
        "status": overall_status,
        "checks": {
            "api": "ok",
            "postgres": postgres_check,
            "redis": redis_check,
            "celery": celery_check
        }
    }