import logging
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import config
from extract import RawRecord
from schema_registry import SchemaRegistry, FieldDef

log = logging.getLogger(__name__)


@dataclass
class TransformedMetric:
    device_uid:   str
    device_type:  str
    device_id:    Optional[int]
    location_id:  Optional[int]
    measurement:  str
    field_name:   str
    value:        Optional[float]
    quality_flag: str
    severity:     str
    note:         str
    timestamp:    float
    tags:         dict = field(default_factory=dict)


_WINDOW_SIZE = 30
_history: dict[str, deque] = {}


def _zscore_check(key: str, value: float) -> tuple[bool, float]:
    if key not in _history:
        _history[key] = deque(maxlen=_WINDOW_SIZE)
    window = _history[key]
    if len(window) < 5:
        window.append(value)
        return True, 0.0
    mean = sum(window) / len(window)
    std  = math.sqrt(sum((x - mean) ** 2 for x in window) / len(window))
    
    min_std = max(1.0, 0.02 * abs(mean))
    effective_std = max(std, min_std)
    
    z = abs(value - mean) / effective_std
    if z <= config.ZSCORE_THRESHOLD:
        window.append(value)
        return True, round(z, 2)
    return False, round(z, 2)


def _validate_field(
    field_def: FieldDef,
    value: float,
    device_uid: str,
) -> tuple[str, str, str]:
    if field_def.name == "soc_pct" and value < 15.0:
        sev = "critical" if value < 10.0 else "warning"
        return "out_of_range", sev, f"{field_def.name}={value:.2f}{field_def.unit} - low battery"
        
    if "temperature" in field_def.name or field_def.name == "temperature_c":
        if value > 50.0:
            sev = "critical" if value > 60.0 else "warning"
            return "out_of_range", sev, f"{field_def.name}={value:.2f}{field_def.unit} - high temperature"

    if field_def.emergency_low is not None and value <= field_def.emergency_low:
        return "out_of_range", "emergency", f"{field_def.name}={value:.2f}{field_def.unit} - emergency low"
    if field_def.critical_low is not None and value <= field_def.critical_low:
        return "out_of_range", "critical", f"{field_def.name}={value:.2f}{field_def.unit} - critical low"
    if field_def.val_min is not None and value < field_def.val_min:
        return "out_of_range", "warning", f"{field_def.name}={value:.2f}{field_def.unit} below min [{field_def.val_min}]"

    if field_def.emergency_high is not None and value >= field_def.emergency_high:
        return "out_of_range", "emergency", f"{field_def.name}={value:.2f}{field_def.unit} - emergency high"
    if field_def.critical_high is not None and value >= field_def.critical_high:
        return "out_of_range", "critical", f"{field_def.name}={value:.2f}{field_def.unit} - critical high"
    if field_def.val_max is not None and value > field_def.val_max:
        return "out_of_range", "warning", f"{field_def.name}={value:.2f}{field_def.unit} above max [{field_def.val_max}]"

    # Z-score spike check
    zscore_key = f"{device_uid}:{field_def.name}"
    is_ok, z = _zscore_check(zscore_key, value)
    if not is_ok:
        return (
            "spike",
            "warning",
            f"Spike detected: {field_def.name}={value:.2f} {field_def.unit}, z={z:.1f}",
        )

    return "ok", "info", ""


def _normalize(record: RawRecord, fields: list[FieldDef]) -> list[dict]:
    preliminary = []
    for fd in fields:
        raw_value = record.data.get(fd.name)

        if raw_value is None:
            if fd.required:
                log.warning(
                    "Missing required field %s.%s (%s)",
                    record.device_uid, fd.name, fd.description,
                )
                preliminary.append({
                    "field_def":   fd,
                    "value":       None,
                    "quality_flag": "missing",
                    "severity":    "warning",
                    "note":        f"Required field '{fd.name}' is missing",
                })
            continue

        if not isinstance(raw_value, (int, float)):
            log.warning(
                "Invalid type for %s.%s: %s",
                record.device_uid, fd.name, type(raw_value),
            )
            continue

        quality_flag, severity, note = _validate_field(fd, float(raw_value), record.device_uid)

        if quality_flag == "spike":
            log.warning(
                "Spike on %s.%s=%.2f",
                record.device_uid, fd.name, raw_value,
            )

        preliminary.append({
            "field_def":    fd,
            "value":        float(raw_value),
            "quality_flag": quality_flag,
            "severity":     severity,
            "note":         note,
        })

    return preliminary


def _enrich_and_aggregate(
    preliminary: list[dict],
    record:      RawRecord,
) -> list[TransformedMetric]:
    result: list[TransformedMetric] = []

    for item in preliminary:
        fd: FieldDef = item["field_def"]
        result.append(TransformedMetric(
            device_uid   = record.device_uid,
            device_type  = record.device_type,
            device_id    = record.device_id,
            location_id  = record.location_id,
            measurement  = f"{record.device_type}_{fd.name}",
            field_name   = "value",
            value        = item["value"],
            quality_flag = item["quality_flag"],
            severity     = item["severity"],
            note         = item["note"],
            timestamp    = record.timestamp_server,
            tags         = {
                "device_uid":   record.device_uid,
                "device_type":  record.device_type,
                "location_id":  str(record.location_id or ""),
                "quality_flag": item["quality_flag"],
                "unit":         fd.unit,
            },
        ))
        
    values = {item["field_def"].name: item["value"]
              for item in preliminary if item["value"] is not None}

    soc = values.get("soc_pct")
    v   = values.get("voltage_v") or values.get("battery_voltage_v")
    if soc is not None and v is not None:
        energy_wh = round((soc / 100) * 200 * v, 1)
        result.append(_make_derived(record, "energy_wh", energy_wh,
                                    "Wh", "Derived remaining energy"))

    pv   = values.get("pv_power_w")
    load = values.get("ac_output_power_w")
    if pv is not None and load is not None:
        balance = round(pv - load, 1)
        result.append(_make_derived(record, "power_balance_w", balance,
                                    "W", "Derived power balance"))

    return result


def _make_derived(record: RawRecord, name: str, value: float,
                  unit: str, desc: str) -> TransformedMetric:
    return TransformedMetric(
        device_uid   = record.device_uid,
        device_type  = record.device_type,
        device_id    = record.device_id,
        location_id  = record.location_id,
        measurement  = f"{record.device_type}_{name}",
        field_name   = "value",
        value        = value,
        quality_flag = "ok",
        severity     = "info",
        note         = desc,
        timestamp    = record.timestamp_server,
        tags         = {"device_uid": record.device_uid,
                        "device_type": record.device_type,
                        "quality_flag": "ok", "unit": unit},
    )


def transform(record: RawRecord, schema_registry: SchemaRegistry) -> list[TransformedMetric]:
    base_fields = schema_registry.get_fields(record.device_type)
    if not base_fields:
        log.warning("Schema for '%s' is empty", record.device_type)
        return []

    fields = schema_registry.apply_device_overrides(base_fields, record.device_meta)

    preliminary = _normalize(record, fields)
    metrics = _enrich_and_aggregate(preliminary, record)

    anom = sum(1 for m in metrics if m.quality_flag != "ok")
    if anom:
        log.info(
            "Transform %s: %d metrics, %d anomalies",
            record.device_uid, len(metrics), anom,
        )
    return metrics
