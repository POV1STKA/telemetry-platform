from typing import Optional, Annotated
import psycopg2.extras
from fastapi import APIRouter, Depends, Query, HTTPException

from auth import CurrentUser
from deps import get_pg

router = APIRouter(prefix="/events", tags=["Events"])

_VALID_SEVERITIES = {"info", "warning", "critical", "emergency"}


@router.get("", summary="Get list of events with paginated filtering")
def list_events(
    _user: CurrentUser,
    pg=Depends(get_pg),
    severity: Optional[str]  = Query(None, description="Filter by severity level"),
    device_uid: Optional[str] = Query(None, description="Filter by target device UID"),
    llm_only: bool            = Query(False, description="Filter events analyzed by LLM only"),
    limit: int                = Query(20, ge=1, le=100),
    offset: int               = Query(0, ge=0),
):
    if severity and severity not in _VALID_SEVERITIES:
        raise HTTPException(status_code=400, detail=f"Invalid severity level. Valid options: {_VALID_SEVERITIES}")

    cond, params = [], []

    if severity:
        cond.append("e.severity = %s")
        params.append(severity)
    if device_uid:
        cond.append("d.device_uid = %s")
        params.append(device_uid)
    if llm_only:
        cond.append("e.llm_explanation IS NOT NULL")

    where = f"WHERE {' AND '.join(cond)}" if cond else ""
    base_join = "FROM event_log e LEFT JOIN devices d ON d.id = e.device_id"

    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT COUNT(*) AS cnt {base_join} {where}", params)
        total: int = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT
                e.id, e.event_type, e.severity,
                e.description, e.llm_explanation, e.command_json,
                e.command_ack, e.created_at,
                d.device_uid, d.name AS device_name, d.device_type
            {base_join}
            {where}
            ORDER BY e.created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        rows = [dict(r) for r in cur.fetchall()]

    for row in rows:
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat()

    return {
        "events": rows,
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }


@router.get("/stats", summary="Get rolling 24-hour event aggregation stats")
def event_stats(
    _user: CurrentUser,
    pg=Depends(get_pg),
):
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT
                severity,
                COUNT(*) AS total,
                COUNT(llm_explanation) AS llm_processed
            FROM event_log
            WHERE created_at >= NOW() - INTERVAL '24 hours'
            GROUP BY severity
            ORDER BY severity
        """)
        by_severity = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(llm_explanation) AS llm_processed,
                COUNT(command_json) AS with_command
            FROM event_log
            WHERE created_at >= NOW() - INTERVAL '24 hours'
        """)
        summary = dict(cur.fetchone())

    return {
        "period_hours": 24,
        "summary":      summary,
        "by_severity":  by_severity,
    }


@router.get("/{event_id}", summary="Get detailed fields of a specific event")
def get_event(
    event_id: int,
    _user: CurrentUser,
    pg=Depends(get_pg),
):
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                e.id, e.event_type, e.severity,
                e.description, e.llm_explanation, e.command_json,
                e.command_ack, e.created_at,
                d.device_uid, d.name AS device_name, d.device_type
            FROM event_log e
            LEFT JOIN devices d ON d.id = e.device_id
            WHERE e.id = %s
            """,
            (event_id,),
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Event #{event_id} not found")

    result = dict(row)
    if result.get("created_at"):
        result["created_at"] = result["created_at"].isoformat()

    return result
