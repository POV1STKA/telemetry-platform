import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extras
import redis as redis_lib
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

import config
from transform import TransformedMetric

log = logging.getLogger(__name__)


@dataclass
class LoadContext:
    influx_write_api: object
    redis_client:     redis_lib.Redis
    pg_conn:          object


def build_load_context() -> LoadContext:
    influx = InfluxDBClient(
        url=config.INFLUX_URL,
        token=config.INFLUX_TOKEN,
        org=config.INFLUX_ORG,
    )
    write_api = influx.write_api(write_options=SYNCHRONOUS)
    log.info("InfluxDB connected: %s / %s", config.INFLUX_URL, config.INFLUX_BUCKET)

    r = redis_lib.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        decode_responses=True,
    )
    r.ping()
    log.info("Redis connected: %s:%s", config.REDIS_HOST, config.REDIS_PORT)

    pg = psycopg2.connect(config.PG_DSN)
    pg.autocommit = True
    log.info("PostgreSQL connected")

    return LoadContext(
        influx_write_api=write_api,
        redis_client=r,
        pg_conn=pg,
    )


def _load_influx(ctx: LoadContext, metrics: list[TransformedMetric]) -> int:
    points = []
    for m in metrics:
        point = (
            Point(m.measurement)
            .tag("device_uid",   m.device_uid)
            .tag("device_type",  m.device_type)
            .tag("quality_flag", m.quality_flag)
            .tag("severity",     m.severity)
            .field("value",      m.value)
            .time(int(m.timestamp * 1_000_000_000), WritePrecision.NS)
        )
        if m.note:
            point = point.field("note", m.note)
        points.append(point)

    if points:
        ctx.influx_write_api.write(
            bucket=config.INFLUX_BUCKET,
            org=config.INFLUX_ORG,
            record=points,
        )
    return len(points)


def _load_redis(ctx: LoadContext, metrics: list[TransformedMetric]) -> None:
    if not metrics:
        return

    uid = metrics[0].device_uid
    pipe = ctx.redis_client.pipeline()

    latest_key = f"device:{uid}:latest"
    latest_data = {"timestamp": metrics[0].timestamp, "device_type": metrics[0].device_type}

    for m in metrics:
        if m.quality_flag != "spike" and m.value is not None:
            field_short = m.measurement.replace(f"{m.device_type}_", "", 1)
            latest_data[field_short] = m.value
            latest_data[f"{field_short}_flag"] = m.quality_flag

            window_key = f"device:{uid}:window:{m.measurement}"
            pipe.rpush(window_key, json.dumps({
                "ts": m.timestamp,
                "v":  m.value,
                "f":  m.quality_flag,
            }))
            pipe.ltrim(window_key, -config.REDIS_WINDOW, -1)
            pipe.expire(window_key, config.REDIS_TTL_SEC)

        if m.quality_flag != "ok":
            anom_key = f"device:{uid}:anomalies"
            pipe.rpush(anom_key, json.dumps({
                "ts":       m.timestamp,
                "field":    m.measurement,
                "value":    m.value,
                "flag":     m.quality_flag,
                "severity": m.severity,
                "note":     m.note,
            }))
            pipe.ltrim(anom_key, -50, -1)
            pipe.expire(anom_key, config.REDIS_TTL_SEC * 6)

    pipe.hset(latest_key, mapping={k: str(v) for k, v in latest_data.items()})
    pipe.expire(latest_key, config.REDIS_TTL_SEC)
    pipe.execute()


_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}


def _load_events(ctx: LoadContext, metrics: list[TransformedMetric]) -> int:
    anomalies = [m for m in metrics if m.quality_flag != "ok"]
    if not anomalies:
        return 0

    top = max(anomalies, key=lambda m: _SEVERITY_ORDER.get(m.severity, 0))
    if top.severity == "info":
        return 0

    notes = "; ".join(
        f"{m.measurement}={m.value:.2f} ({m.note})"
        for m in anomalies if m.note
    )

    with ctx.pg_conn.cursor() as cur:
        cur.execute("""
            INSERT INTO event_log
                (event_type, severity, device_id, description)
            VALUES
                ('anomaly', %s, %s, %s)
        """, (
            top.severity,
            top.device_id,
            f"[ETL] {top.device_uid}: {notes[:500]}",
        ))

    log.info(
        "Event log saved to PostgreSQL for %s (%s)",
        top.device_uid, top.severity.upper()
    )
    return 1


def load(ctx: LoadContext, metrics: list[TransformedMetric]) -> dict:
    if not metrics:
        return {"influx": 0, "redis": 0, "events": 0}

    stats = {"influx": 0, "redis": 0, "events": 0}

    try:
        stats["influx"] = _load_influx(ctx, metrics)
    except Exception as e:
        log.error("InfluxDB writing error: %s", e)

    try:
        _load_redis(ctx, metrics)
        stats["redis"] = len(metrics)
    except Exception as e:
        log.error("Redis writing error: %s", e)

    try:
        stats["events"] = _load_events(ctx, metrics)
    except Exception as e:
        log.error("PostgreSQL event log writing error: %s", e)

    return stats
