import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras

import config
from context_builder import build_context
from google_client import GeminiError, generate
from prompt_builder import build_prompt

log = logging.getLogger(__name__)

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2, "emergency": 3}


def _max_severity(a: Optional[str], b: Optional[str]) -> Optional[str]:
    order_a = _SEVERITY_ORDER.get(a, -1) if a else -1
    order_b = _SEVERITY_ORDER.get(b, -1) if b else -1
    if order_a >= order_b:
        return a if order_a >= 0 else b
    return b


def process_pending_events(pg_conn, redis_client) -> int:
    events = _fetch_pending_events(pg_conn)
    if not events:
        log.debug("No new events for analysis.")
        return 0

    log.info("Found %d events for LLM analysis.", len(events))
    processed = 0

    for event in events:
        success = _process_single_event(event, pg_conn, redis_client)
        if success:
            processed += 1
        time.sleep(0.5)

    log.info("Processed %d/%d events.", processed, len(events))
    return processed


def _fetch_pending_events(pg_conn) -> list[dict]:
    min_order = _SEVERITY_ORDER.get(config.MIN_SEVERITY, 1)
    eligible = [s for s, o in _SEVERITY_ORDER.items() if o >= min_order]

    if not eligible:
        return []

    placeholders = ",".join(["%s"] * len(eligible))
    query = f"""
        SELECT
            e.id,
            e.event_type,
            e.severity,
            e.device_id,
            e.description,
            e.created_at,
            d.device_uid,
            d.device_type,
            d.name AS device_name
        FROM event_log e
        LEFT JOIN devices d ON d.id = e.device_id
        WHERE e.llm_explanation IS NULL
          AND e.severity IN ({placeholders})
        ORDER BY e.created_at ASC
        LIMIT %s
    """

    try:
        with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, eligible + [config.BATCH_SIZE])
            return [dict(row) for row in cur.fetchall()]
    except psycopg2.OperationalError:
        raise
    except Exception as e:
        log.error("Error reading event_log: %s", e)
        return []


def _process_single_event(
    event: dict,
    pg_conn,
    redis_client,
) -> bool:
    event_id    = event["id"]
    device_uid  = event.get("device_uid") or "unknown"
    description = event.get("description", "")
    severity    = event.get("severity", "warning")

    log.info("Processing event #%d: device=%s, severity=%s", event_id, device_uid, severity)

    try:
        context = build_context(device_uid, redis_client)
    except Exception as e:
        log.warning("Redis unavailable, empty context: %s", e)
        context = {"device_uid": device_uid, "current": {}, "anomalies": [], "trends": {}}

    prompt = build_prompt(context, description)

    try:
        raw_response = generate(prompt)
    except GeminiError as e:
        log.warning(
            "Gemini API unavailable (event #%d): %s. Rule-based fallback activated.",
            event_id, e,
        )
        parsed = _rule_based_fallback(context, description, severity)
        return _update_event_log(
            pg_conn, event_id,
            explanation=parsed.get("explanation", ""),
            command_json=parsed.get("command"),
            new_severity=parsed.get("severity_assessment"),
            recommended_action=parsed.get("recommended_action"),
            root_cause=parsed.get("root_cause"),
        )

    parsed = _parse_llm_response(raw_response)

    explanation  = parsed.get("explanation") or raw_response[:1000]
    command_json = parsed.get("command")
    llm_severity = parsed.get("severity_assessment")

    if command_json and isinstance(command_json, dict):
        if command_json.get("type") in (None, "none", ""):
            command_json = None
    else:
        command_json = None

    current_severity = event.get("severity", "warning")
    new_severity = _max_severity(current_severity, llm_severity)
    if new_severity != current_severity:
        log.info("Escalation of event #%d: %s -> %s", event_id, current_severity, new_severity)

    success = _update_event_log(
        pg_conn, event_id,
        explanation=explanation,
        command_json=command_json,
        new_severity=new_severity,
        recommended_action=parsed.get("recommended_action"),
        root_cause=parsed.get("root_cause"),
    )

    if success:
        log.info("Event #%d processed. Explanation: %s", event_id, explanation[:80])

    return success


def _parse_llm_response(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if md_match:
        try:
            return json.loads(md_match.group(1))
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    log.warning("Failed to parse JSON from LLM response: %s", raw[:200])
    return {}


def _update_event_log(
    pg_conn,
    event_id: int,
    explanation: str,
    command_json: Optional[dict],
    new_severity: Optional[str],
    recommended_action: Optional[str],
    root_cause: Optional[str],
) -> bool:
    parts = []
    if new_severity and new_severity in _SEVERITY_ORDER:
        parts.append(f"[LLM оцінка: {new_severity.upper()}]")
    if explanation:
        parts.append(explanation)
    if root_cause:
        parts.append(f"Причина: {root_cause}")
    if recommended_action:
        parts.append(f"Рекомендація: {recommended_action}")
    full_explanation = "\n".join(parts)

    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                """
                UPDATE event_log
                SET
                    llm_explanation = %s,
                    command_json    = %s,
                    severity        = COALESCE(%s, severity)
                WHERE id = %s
                """,
                (
                    full_explanation[:2000],
                    json.dumps(command_json, ensure_ascii=False) if command_json else None,
                    new_severity,
                    event_id,
                ),
            )
        return True
    except psycopg2.OperationalError:
        raise
    except Exception as e:
        log.error("Error updating event_log for event #%d: %s", event_id, e)
        try:
            pg_conn.rollback()
        except Exception:
            pass
        return False


def _rule_based_fallback(
    context: dict,
    description: str,
    event_severity: str,
) -> dict:
    current = context.get("current", {})
    issues  = []
    cmd_type = "none"
    cmd_params: dict = {}
    severity = event_severity

    soc = current.get("soc_pct")
    if soc is not None:
        if soc < 10:
            issues.append(f"Критично низький заряд батареї: {soc:.1f}% (< 10%)")
            cmd_type   = "disconnect_load"
            cmd_params = {}
            severity   = _max_severity(severity, "critical")
        elif soc < 20:
            issues.append(f"Низький заряд батареї: {soc:.1f}% (< 20%)")
            cmd_type   = "reduce_load"
            cmd_params = {"reduction_pct": 40}
            severity   = _max_severity(severity, "warning")

    temp = current.get("temperature_c")
    if temp is not None:
        if temp > 60:
            issues.append(f"Критичний перегрів: {temp:.1f}°C (> 60°C)")
            cmd_type   = "disconnect_load"
            severity   = _max_severity(severity, "emergency")
        elif temp > 50:
            issues.append(f"Підвищена температура: {temp:.1f}°C (> 50°C)")
            if cmd_type == "none":
                cmd_type   = "reduce_load"
                cmd_params = {"reduction_pct": 30}
            severity = _max_severity(severity, "critical")

    load = current.get("ac_output_power_w")
    if load is not None and load > 3000:
        issues.append(f"Перевантаження: {load:.0f} Вт (> 3000 Вт)")
        if cmd_type == "none":
            cmd_type   = "reduce_load"
            cmd_params = {"reduction_pct": 25}
        severity = _max_severity(severity, "warning")

    voltage = current.get("voltage_v") or current.get("dc_voltage_v")
    if voltage is not None:
        if voltage < 44:
            issues.append(f"Низька напруга шини: {voltage:.2f} В (< 44 В)")
            severity = _max_severity(severity, "critical")
        elif voltage > 60:
            issues.append(f"Висока напруга шини: {voltage:.2f} В (> 60 В)")
            severity = _max_severity(severity, "critical")

    if issues:
        explanation = (
            "[РЕЖИМ ДЕГРАДАЦІЇ — rule-based аналіз, LLM недоступна]\n"
            "Виявлені проблеми:\n"
            + "\n".join(f"• {i}" for i in issues)
        )
        root_cause        = "Автоматично виявлено на основі порогових правил."
        recommended_action = (
            "Перевірте стан системи вручну. "
            "Аналіз ШІ буде виконано автоматично після відновлення зв'язку."
        )
    else:
        explanation = (
            f"[РЕЖИМ ДЕГРАДАЦІЇ — rule-based аналіз]\n"
            f"Подія: {description}\n"
            "Порогові перевірки пройдено без критичних відхилень. "
            "Аналіз ШІ виконається після відновлення зв'язку."
        )
        root_cause         = "Не визначено (LLM недоступна)."
        recommended_action = "Спостерігати за розвитком ситуації."
        cmd_type           = "none"

    command = None
    if cmd_type != "none":
        command = {
            "type":   cmd_type,
            "params": cmd_params,
            "reason": f"Автоматичне рішення rule-based модуля (Gemini недоступний): {', '.join(issues)}",
        }

    log.info("Rule-based fallback solution: severity=%s, cmd=%s, issues=%d", severity, cmd_type, len(issues))

    return {
        "severity_assessment": severity,
        "explanation":         explanation,
        "root_cause":          root_cause,
        "recommended_action":  recommended_action,
        "command":             command,
    }
