import logging
from dataclasses import dataclass
from typing import Optional

import psycopg2.extras

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FieldDef:
    name:        str
    required:    bool
    val_min:     Optional[float]
    val_max:     Optional[float]
    unit:        str
    description: str
    emergency_low:  Optional[float] = None
    critical_low:   Optional[float] = None
    critical_high:  Optional[float] = None
    emergency_high: Optional[float] = None


class SchemaRegistry:
    def __init__(self, pg_conn) -> None:
        self._pg_conn = pg_conn
        self._schemas: dict[str, list[FieldDef]] = {}
        self.refresh()

    def refresh(self) -> None:
        schemas: dict[str, list[FieldDef]] = {}
        with self._pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT device_type, fields FROM device_schemas")
            rows = cur.fetchall()
        for row in rows:
            schemas[row["device_type"]] = [
                self._parse_field(f) for f in row["fields"]
            ]
        self._schemas = schemas
        log.info(
            "Loaded %d schemas: %s",
            len(schemas), list(schemas.keys()),
        )

    def get_fields(self, device_type: str) -> list[FieldDef]:
        return self._schemas.get(device_type, [])

    def is_known_type(self, device_type: str) -> bool:
        return device_type in self._schemas

    def known_types(self) -> list[str]:
        return list(self._schemas.keys())

    def apply_device_overrides(
        self,
        fields: list[FieldDef],
        device_meta: dict,
    ) -> list[FieldDef]:
        _OVERRIDE_MAP = {
            "voltage_min": ("dc_voltage_v", "battery_voltage_v", "voltage_v"),
            "voltage_max": ("dc_voltage_v", "battery_voltage_v", "voltage_v"),
            "current_min": ("dc_current_a", "charge_current_a",  "current_a"),
            "current_max": ("dc_current_a", "charge_current_a",  "current_a"),
            "temp_min":    ("temperature_c",),
            "temp_max":    ("temperature_c",),
        }

        overrides: dict[str, dict] = {}
        for col, targets in _OVERRIDE_MAP.items():
            val = device_meta.get(col)
            if val is None:
                continue
            for target in targets:
                overrides.setdefault(target, {})
                if col.endswith("_min"):
                    overrides[target]["val_min"] = float(val)
                else:
                    overrides[target]["val_max"] = float(val)

        if not overrides:
            return fields

        result = []
        for f in fields:
            ov = overrides.get(f.name)
            if ov:
                f = FieldDef(
                    name           = f.name,
                    required       = f.required,
                    val_min        = ov.get("val_min", f.val_min),
                    val_max        = ov.get("val_max", f.val_max),
                    unit           = f.unit,
                    description    = f.description,
                    emergency_low  = f.emergency_low,
                    critical_low   = f.critical_low,
                    critical_high  = f.critical_high,
                    emergency_high = f.emergency_high,
                )
            result.append(f)
        return result

    @staticmethod
    def _parse_field(raw: dict) -> FieldDef:
        return FieldDef(
            name           = raw["name"],
            required       = raw.get("required", True),
            val_min        = raw.get("min"),
            val_max        = raw.get("max"),
            unit           = raw.get("unit", ""),
            description    = raw.get("description", ""),
            emergency_low  = raw.get("emergency_low"),
            critical_low   = raw.get("critical_low"),
            critical_high  = raw.get("critical_high"),
            emergency_high = raw.get("emergency_high"),
        )
