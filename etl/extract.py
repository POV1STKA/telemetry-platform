import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

@dataclass
class RawRecord:
    device_uid:       str
    device_type:      str
    timestamp_device: float
    data:             dict

    device_id:    Optional[int]   = None
    location_id:  Optional[int]   = None
    device_meta:  dict            = field(default_factory=dict)

    timestamp_server: float = field(default_factory=time.time)
    mqtt_topic:       str   = ""


_device_cache: dict[str, dict] = {}


def refresh_device_cache(pg_conn) -> None:
    global _device_cache
    with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT device_uid, id, location_id,
                   voltage_min, voltage_max,
                   current_min, current_max,
                   temp_min,    temp_max
            FROM devices
            WHERE is_active = TRUE
        """)
        rows = cur.fetchall()
    _device_cache = {row["device_uid"]: dict(row) for row in rows}
    log.info("Device cache refreshed: %d records", len(_device_cache))


def extract(topic: str, raw_bytes: bytes, pg_conn, schema_registry) -> Optional[RawRecord]:
    try:
        payload = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.warning("JSON decode error on %s: %s", topic, e)
        return None

    if "simulator/status" in topic:
        return None

    for required in ("device_uid", "device_type", "data"):
        if required not in payload:
            log.warning("Missing required field '%s' in packet from %s", required, topic)
            return None

    if not isinstance(payload["data"], dict):
        log.warning("'data' field must be a dictionary, got %s", type(payload["data"]))
        return None

    device_type = payload["device_type"]
    if not schema_registry.is_known_type(device_type):
        log.warning("Unknown device_type '%s'. Refreshing schema registry...", device_type)
        schema_registry.refresh()
        if not schema_registry.is_known_type(device_type):
            log.warning("Device type '%s' is not registered in schema registry", device_type)
            return None

    if not _device_cache:
        refresh_device_cache(pg_conn)

    uid         = payload["device_uid"]
    device_meta = _device_cache.get(uid)

    if device_meta is None:
        log.warning("Device '%s' is not registered in the system", uid)

    record = RawRecord(
        device_uid        = uid,
        device_type       = device_type,
        timestamp_device  = float(payload.get("timestamp_device", time.time())),
        data              = payload["data"],
        mqtt_topic        = topic,
        device_meta       = device_meta or {},
    )
    if device_meta:
        record.device_id   = device_meta["id"]
        record.location_id = device_meta["location_id"]

    return record
