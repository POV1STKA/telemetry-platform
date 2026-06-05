import json
import logging
from typing import Any

import config

log = logging.getLogger(__name__)


def build_context(device_uid: str, redis_client) -> dict[str, Any]:
    context: dict[str, Any] = {
        "device_uid": device_uid,
        "current":    {},
        "anomalies":  [],
        "trends":     {},
    }

    latest_key = f"device:{device_uid}:latest"
    try:
        raw_latest = redis_client.hgetall(latest_key)
        if raw_latest:
            context["current"] = _parse_latest(raw_latest)
        else:
            log.debug("No latest data found for device: %s", device_uid)
    except Exception as e:
        log.warning("Error reading latest for device %s: %s", device_uid, e)

    anom_key = f"device:{device_uid}:anomalies"
    try:
        raw_anomalies = redis_client.lrange(
            anom_key, -config.REDIS_ANOMALY_LIMIT, -1
        )
        context["anomalies"] = [
            json.loads(a) for a in raw_anomalies if a
        ]
    except Exception as e:
        log.warning("Error reading anomalies for device %s: %s", device_uid, e)

    window_pattern = f"device:{device_uid}:window:*"
    try:
        window_keys = redis_client.keys(window_pattern)
        for key in window_keys:
            measurement = key.split(":window:")[-1] if ":window:" in key else key
            raw_window = redis_client.lrange(key, -config.REDIS_WINDOW_LIMIT, -1)
            points = []
            for item in raw_window:
                try:
                    points.append(json.loads(item))
                except json.JSONDecodeError:
                    pass
            if points:
                context["trends"][measurement] = points
    except Exception as e:
        log.warning("Error reading window data for device %s: %s", device_uid, e)

    return context


def _parse_latest(raw: dict) -> dict:
    parsed = {}
    for k, v in raw.items():
        try:
            parsed[k] = float(v)
        except (ValueError, TypeError):
            parsed[k] = v
    return parsed
