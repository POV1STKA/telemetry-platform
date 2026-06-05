import json
import re
import logging
from typing import Annotated, Optional

import psycopg2.extras
from fastapi import APIRouter, Depends, Query, HTTPException

import config
from auth import CurrentUser
from deps import get_pg, get_redis

router = APIRouter(prefix="/devices", tags=["Devices"])
log = logging.getLogger(__name__)

_UID_RE   = re.compile(r'^[a-zA-Z0-9_\-]+$')
_FIELD_RE = re.compile(r'^[a-zA-Z0-9_]+$')


def _validate_uid(uid: str) -> None:
    if not _UID_RE.match(uid):
        raise HTTPException(status_code=400, detail="Invalid characters in device_uid")


def _validate_field(field: str) -> None:
    if not _FIELD_RE.match(field):
        raise HTTPException(status_code=400, detail="Invalid characters in field name")


@router.get("", summary="Get all devices with latest states")
def list_devices(
    _user: CurrentUser,
    pg=Depends(get_pg),
    redis=Depends(get_redis),
):
    with pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT id, device_uid, name, device_type, is_active,
                   voltage_min, voltage_max, temp_min, temp_max
            FROM devices
            ORDER BY device_type, name
        """)
        devices = [dict(r) for r in cur.fetchall()]

    for device in devices:
        uid = device["device_uid"]

        raw = redis.hgetall(f"device:{uid}:latest")
        state: dict = {}
        for k, v in raw.items():
            try:
                state[k] = float(v)
            except (ValueError, TypeError):
                state[k] = v
        device["current_state"] = state

        anom_raw = redis.lrange(f"device:{uid}:anomalies", 0, -1)
        anomalies = []
        for a in anom_raw:
            try:
                anomalies.append(json.loads(a))
            except Exception:
                pass

        sev_order = {"ok": 0, "info": 1, "warning": 2, "critical": 3, "emergency": 4}
        max_sev = "ok"
        for anom in anomalies:
            if sev_order.get(anom.get("severity", "ok"), 0) > sev_order.get(max_sev, 0):
                max_sev = anom.get("severity", "ok")

        device["anomaly_count"] = len(anomalies)
        device["max_severity"]  = max_sev
        device["is_online"]     = bool(state)

    return {"devices": devices, "total": len(devices)}


@router.get("/{uid}/state", summary="Get detailed real-time state of a device")
def device_state(
    uid: str,
    _user: CurrentUser,
    redis=Depends(get_redis),
):

    _validate_uid(uid)

    raw = redis.hgetall(f"device:{uid}:latest")
    is_online = bool(raw)
    state: dict = {}
    for k, v in raw.items():
        try:
            state[k] = float(v)
        except (ValueError, TypeError):
            state[k] = v

    anom_raw = redis.lrange(f"device:{uid}:anomalies", 0, 19)
    anomalies = []
    for a in anom_raw:
        try:
            anomalies.append(json.loads(a))
        except Exception:
            pass

    pattern = f"device:{uid}:window:*"
    trends: dict = {}
    for wk in redis.scan_iter(pattern, count=100):
        field_name = wk.split(":window:")[-1]
        raw_points = redis.lrange(wk, 0, -1)
        points = []
        for p in raw_points:
            try:
                points.append(json.loads(p))
            except Exception:
                pass
        if points:
            trends[field_name] = points

    return {
        "device_uid": uid,
        "is_online":  is_online,
        "current":    state,
        "anomalies":  anomalies,
        "trends":     trends,
    }


@router.get("/{uid}/history", summary="Fetch historical telemetry from InfluxDB")
def device_history(
    uid: str,
    field: str = Query("soc_pct", description="Target telemetry variable name"),
    minutes: int = Query(60, ge=5, le=1440, description="History window depth in minutes"),
    _user: CurrentUser = None,
):
    _validate_uid(uid)
    _validate_field(field)

    try:
        from influxdb_client import InfluxDBClient
        with InfluxDBClient(
            url=config.INFLUX_URL,
            token=config.INFLUX_TOKEN,
            org=config.INFLUX_ORG,
        ) as client:
            flux = (
                f'from(bucket: "{config.INFLUX_BUCKET}")\n'
                f'  |> range(start: -{minutes}m)\n'
                f'  |> filter(fn: (r) => r.device_uid == "{uid}")\n'
                f'  |> filter(fn: (r) => r._field == "value")\n'
                f'  |> filter(fn: (r) => r._measurement =~ /_{field}$/)\n'
                f'  |> sort(columns: ["_time"])\n'
                f'  |> limit(n: 500)'
            )
            tables = client.query_api().query(flux)
            points = []
            for table in tables:
                for record in table.records:
                    points.append({
                        "ts":    record.get_time().timestamp(),
                        "value": record.get_value(),
                        "flag":  record.values.get("quality_flag", "ok"),
                    })
    except Exception as exc:
        log.warning("InfluxDB history query failed for %s/%s: %s", uid, field, exc)
        raise HTTPException(status_code=503, detail="Historical metrics database is temporarily unavailable.")

    return {
        "device_uid": uid,
        "field":      field,
        "minutes":    minutes,
        "points":     points,
        "count":      len(points),
    }
