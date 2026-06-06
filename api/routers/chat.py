import json
import logging
from datetime import datetime, timezone

import httpx
import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import config
from auth import CurrentUser
from deps import get_pg, get_redis

router = APIRouter(prefix="/chat", tags=["System Chat"])
log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="Operator query text")
    device_uid: str | None = Field(None, description="Optional target device context")


class ChatResponse(BaseModel):
    reply:      str
    model:      str
    device_uid: str | None
    ts:         str


_CHAT_SYSTEM = """\
You are an expert telemetry operator assistant for an intelligent backup energy system. \
Answer strictly in Ukrainian, concisely and technically. \
Provide specific details about anomalies, counts, nature of problems, and recommendations based on the provided SYSTEM CONTEXT (recent database event logs and real-time state). \
If context is missing or incomplete, base your answer on general energy systems knowledge.\
"""


@router.post("", response_model=ChatResponse, summary="Submit a query about system telemetry")
def chat(
    req: ChatRequest,
    _user: CurrentUser,
    pg=Depends(get_pg),
    redis=Depends(get_redis),
):
    context_parts = []

    try:
        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE severity IN ('critical','emergency'))
                        AS critical_count,
                    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour')
                        AS last_hour
                FROM event_log
            """)
            stats = dict(cur.fetchone())
        context_parts.append(
            f"Platform Event Stats: Total events={stats['total']}, "
            f"Critical/Emergency={stats['critical_count']}, "
            f"Last hour={stats['last_hour']}"
        )
    except Exception as exc:
        log.warning("Could not fetch system database stats: %s", exc)

    # Fetch recent detailed event logs from PostgreSQL (RAG enrichment)
    try:
        with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if req.device_uid:
                cur.execute(
                    """
                    SELECT e.event_type, e.severity, e.description, e.llm_explanation, e.created_at
                    FROM event_log e
                    LEFT JOIN devices d ON d.id = e.device_id
                    WHERE d.device_uid = %s
                    ORDER BY e.created_at DESC
                    LIMIT 5
                    """,
                    (req.device_uid,),
                )
            else:
                cur.execute(
                    """
                    SELECT e.event_type, e.severity, e.description, e.llm_explanation, e.created_at, d.device_uid
                    FROM event_log e
                    LEFT JOIN devices d ON d.id = e.device_id
                    ORDER BY e.created_at DESC
                    LIMIT 5
                    """
                )
            recent_rows = cur.fetchall()
            if recent_rows:
                anom_lines = []
                for row in recent_rows:
                    dev_str = f"Device={row.get('device_uid')} " if not req.device_uid else ""
                    ts_str = row['created_at'].strftime("%Y-%m-%d %H:%M:%S")
                    expl = row['llm_explanation']
                    line = (
                        f"- [{ts_str}] {dev_str}Severity={row['severity']}, Type={row['event_type']}, "
                        f"Desc='{row['description']}'"
                    )
                    if expl:
                        # Include explanation / recommendation (limit size to keep context concise)
                        line += f", Recommendation/Diagnosis='{expl[:250]}'"
                    anom_lines.append(line)
                context_parts.append("Recent system events from database:\n" + "\n".join(anom_lines))
    except Exception as exc:
        log.warning("Could not fetch recent event logs for chat: %s", exc)

    if req.device_uid:
        raw = redis.hgetall(f"device:{req.device_uid}:latest")
        if raw:
            state_str = ", ".join(
                f"{k}={v}" for k, v in list(raw.items())[:10]
                if k not in ("timestamp", "device_type")
            )
            context_parts.append(
                f"Current real-time state for device {req.device_uid}: {state_str}"
            )

            anom_raw = redis.lrange(f"device:{req.device_uid}:anomalies", 0, 4)
            if anom_raw:
                anomalies = []
                for a in anom_raw:
                    try:
                        anomalies.append(json.loads(a))
                    except Exception:
                        pass
                if anomalies:
                    anom_str = "; ".join(
                        f"{a.get('field')}={a.get('value')} [{a.get('flag')}]"
                        for a in anomalies
                    )
                    context_parts.append(f"Real-time anomaly flags in cache: {anom_str}")
        else:
            context_parts.append(f"Device {req.device_uid}: offline or missing Redis data.")

    context_block = "\n".join(context_parts)
    full_prompt = (
        f"{_CHAT_SYSTEM}\n\n"
        + (f"=== SYSTEM CONTEXT ===\n{context_block}\n\n" if context_block else "")
        + f"=== OPERATOR QUERY ===\n{req.message}"
    )

    key = config.GEMINI_API_KEY
    if not key or len(key) < 10:
        raise HTTPException(status_code=503, detail="Gemini Cloud API is not configured on this host.")

    gemini_model = "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{gemini_model}:generateContent?key={key}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": full_prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.3
        }
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            reply = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini API request timed out.")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini API returned error code: {exc.response.status_code}")
    except Exception as exc:
        log.error("Gemini API connection error: %s", exc)
        raise HTTPException(status_code=503, detail="Gemini Cloud reasoning services are currently unavailable.")

    if not reply:
        raise HTTPException(status_code=502, detail="Empty response returned by Gemini.")

    return ChatResponse(
        reply      = reply,
        model      = gemini_model,
        device_uid = req.device_uid,
        ts         = datetime.now(timezone.utc).isoformat(),
    )
