import json
import logging
from typing import Any

log = logging.getLogger(__name__)

_MAX_ANOMALY_CHARS = 1500
_MAX_TREND_CHARS   = 800

SYSTEM_PROMPT = """\
Ти — інтелектуальна система моніторингу сонячної фотоелектричної електростанції.
Твоє завдання — аналізувати аномалії телеметрії та генерувати рекомендації.

ПРАВИЛА:
1. Відповідай ВИКЛЮЧНО валідним JSON — без пояснень поза JSON, без markdown-блоків.
2. JSON повинен точно відповідати наданій схемою.
3. Якщо аномалія незначна — recommended_action може бути "continue_monitoring".
4. Команду (command) генеруй ТІЛЬКИ для рівнів critical або emergency.
5. Пиши explanation та recommended_action українською мовою.
"""

RESPONSE_SCHEMA = """\
{
  "severity_assessment": "info|warning|critical|emergency",
  "explanation": "Детальне пояснення що відбувається з пристроєм та чому це проблема",
  "recommended_action": "Конкретна рекомендована дія оператору",
  "root_cause": "Можлива першопричина аномалії",
  "command": {
    "type": "reduce_load|disconnect_load|charge_limit|alert_operator|none",
    "params": {},
    "reason": "Чому саме ця команда"
  }
}
"""


def build_prompt(context: dict[str, Any], event_description: str) -> str:
    device_uid  = context.get("device_uid", "unknown")
    current     = context.get("current", {})
    anomalies   = context.get("anomalies", [])
    trends      = context.get("trends", {})

    current_section = _format_current_state(current)
    anomalies_section = _format_anomalies(anomalies)
    trends_section = _format_trends(trends)

    prompt = f"""{SYSTEM_PROMPT}

=== ПОДІЯ ===
Пристрій: {device_uid}
Опис події: {event_description}

=== ПОТОЧНИЙ СТАН ПРИСТРОЮ ===
{current_section}

=== ОСТАННІ АНОМАЛІЇ (хронологічно) ===
{anomalies_section}

=== ТРЕНДИ КЛЮЧОВИХ ПОКАЗНИКІВ ===
{trends_section}

=== ЗАВДАННЯ ===
Проаналізуй наведену ситуацію та поверни відповідь строго у форматі JSON:
{RESPONSE_SCHEMA}

Відповідь (тільки JSON, без додаткового тексту):"""

    return prompt


def _format_current_state(current: dict) -> str:
    if not current:
        return "Дані поточного стану відсутні."

    lines = []
    for k, v in current.items():
        if k in ("timestamp", "device_type"):
            continue
        if isinstance(v, float):
            lines.append(f"  {k}: {v:.2f}")
        else:
            lines.append(f"  {k}: {v}")

    return "\n".join(lines) if lines else "Дані поточного стану відсутні."


def _format_anomalies(anomalies: list[dict]) -> str:
    if not anomalies:
        return "Аномалій не зафіксовано."

    lines = []
    for a in anomalies[-10:]:
        field   = a.get("field", "?")
        value   = a.get("value")
        flag    = a.get("flag", "?")
        sev     = a.get("severity", "?")
        note    = a.get("note", "")
        val_str = f"{value:.2f}" if isinstance(value, (int, float)) else str(value)
        lines.append(f"  [{sev.upper()}] {field}={val_str} ({flag}): {note}")

    result = "\n".join(lines)
    if len(result) > _MAX_ANOMALY_CHARS:
        result = result[:_MAX_ANOMALY_CHARS] + "\n  ...(скорочено)"
    return result


def _format_trends(trends: dict[str, list]) -> str:
    if not trends:
        return "Дані трендів відсутні."

    lines = []
    for measurement, points in trends.items():
        if not points:
            continue
        values = [p.get("v") for p in points if p.get("v") is not None]
        if not values:
            continue
        last5 = values[-5:]
        direction = _trend_direction(values)
        vals_str  = ", ".join(f"{v:.2f}" for v in last5)
        lines.append(f"  {measurement}: [{vals_str}] - {direction}")

    result = "\n".join(lines)
    if len(result) > _MAX_TREND_CHARS:
        result = result[:_MAX_TREND_CHARS] + "\n  ...(скорочено)"
    return result if result else "Дані трендів відсутні."


def _trend_direction(values: list[float]) -> str:
    if len(values) < 2:
        return "стабільно"
    delta = values[-1] - values[0]
    pct   = abs(delta) / (abs(values[0]) + 1e-9) * 100
    if pct < 2:
        return "стабільно"
    return f"зростає (+{delta:.2f})" if delta > 0 else f"спадає ({delta:.2f})"
