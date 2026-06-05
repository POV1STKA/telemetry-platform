from fastapi import APIRouter, Depends
import psycopg2.extras
import redis as redis_lib
import httpx

import config
from deps import get_pg, get_redis

router = APIRouter(tags=["Health & Config"])


@router.get("/health", summary="Get status of system dependencies")
async def health(
    pg=Depends(get_pg),
    redis=Depends(get_redis),
):
    status: dict = {}

    try:
        with pg.cursor() as cur:
            cur.execute("SELECT 1")
        status["postgres"] = "ok"
    except Exception as e:
        status["postgres"] = f"error: {e}"

    try:
        redis.ping()
        status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"error: {e}"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{config.INFLUX_URL}/health")
        status["influxdb"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
    except Exception as e:
        status["influxdb"] = f"error: {e}"

    status["gemini_api"] = "ok" if bool(config.GEMINI_API_KEY) else "not_configured"

    overall = "ok" if all(v == "ok" for v in status.values()) else "degraded"
    return {"status": overall, "services": status}


@router.get("/api/auth/config", summary="Retrieve Auth0 settings for front-end configuration")
def auth_config():
    return {
        "domain":    config.AUTH0_DOMAIN,
        "clientId":  config.AUTH0_CLIENT_ID,
        "audience":  config.AUTH0_AUDIENCE,
        "dev_mode":  not bool(config.AUTH0_DOMAIN),
    }


@router.get("/api/system/stats", summary="Get platform global aggregation statistics")
def system_stats(pg=Depends(get_pg)):
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                        AS total_events,
                COUNT(*) FILTER (WHERE severity = 'critical')  AS critical_count,
                COUNT(*) FILTER (WHERE severity = 'emergency') AS emergency_count,
                COUNT(llm_explanation)                          AS llm_processed,
                COUNT(command_json)                             AS commands_generated,
                MAX(created_at)                                 AS last_event_at
            FROM event_log
        """)
        row = dict(cur.fetchone())

    if row.get("last_event_at"):
        row["last_event_at"] = row["last_event_at"].isoformat()

    return row
