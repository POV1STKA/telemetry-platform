import json
import logging
import time
from typing import Optional

import paho.mqtt.client as mqtt
import psycopg2
import psycopg2.extras

import config

log = logging.getLogger(__name__)

ACTIONABLE_TYPES = {"reduce_load", "disconnect_load", "charge_limit"}
ALERT_TYPE = "alert_operator"


class CommandDispatcher:
    def __init__(self, pg_conn, mqtt_host: str, mqtt_port: int):
        self._pg_conn   = pg_conn
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._mqtt_client: Optional[mqtt.Client] = None

    def _get_mqtt_client(self) -> Optional[mqtt.Client]:
        if self._mqtt_client and self._mqtt_client.is_connected():
            return self._mqtt_client

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="command_dispatcher",
        )
        client.on_connect    = self._on_connect
        client.on_disconnect = self._on_disconnect

        try:
            client.connect(self._mqtt_host, self._mqtt_port, keepalive=30)
            client.loop_start()
            time.sleep(0.3)
            self._mqtt_client = client
            return client
        except Exception as exc:
            log.warning("Could not connect to MQTT (%s:%d): %s",
                        self._mqtt_host, self._mqtt_port, exc)
            return None

    @staticmethod
    def _on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            log.info("MQTT connected.")
        else:
            log.error("MQTT connection error: %s", reason_code)

    @staticmethod
    def _on_disconnect(client, userdata, flags, reason_code, properties):
        log.warning("MQTT disconnected (code %s).", reason_code)

    def dispatch_pending(self) -> int:
        pending = self._fetch_pending_commands()
        if not pending:
            return 0

        log.info("Found %d pending commands for dispatch.", len(pending))
        sent = 0

        for row in pending:
            if self._dispatch_one(row):
                sent += 1

        log.info("Dispatched %d/%d commands.", sent, len(pending))
        return sent

    def _fetch_pending_commands(self) -> list[dict]:
        try:
            with self._pg_conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:
                cur.execute("""
                    SELECT
                        e.id,
                        e.severity,
                        e.command_json,
                        e.created_at,
                        d.device_uid,
                        d.device_type,
                        d.name AS device_name
                    FROM event_log e
                    LEFT JOIN devices d ON d.id = e.device_id
                    WHERE e.command_json IS NOT NULL
                      AND e.command_ack  = FALSE
                    ORDER BY e.created_at ASC
                    LIMIT 20
                """)
                return [dict(r) for r in cur.fetchall()]
        except psycopg2.OperationalError:
            raise
        except Exception as exc:
            log.error("Error reading pending commands: %s", exc)
            return []

    def _dispatch_one(self, row: dict) -> bool:
        event_id   = row["id"]
        device_uid = row.get("device_uid") or "unknown"
        raw_cmd    = row.get("command_json")

        try:
            if isinstance(raw_cmd, str):
                cmd = json.loads(raw_cmd)
            elif isinstance(raw_cmd, dict):
                cmd = raw_cmd
            else:
                log.warning("Invalid command_json in event #%d", event_id)
                self._mark_ack(event_id)
                return False
        except Exception as exc:
            log.warning("JSON parse error in event #%d: %s", event_id, exc)
            self._mark_ack(event_id)
            return False

        cmd_type = cmd.get("type", "none")

        if cmd_type in (None, "none", ""):
            log.debug("Command is 'none' for event #%d - skipping.", event_id)
            self._mark_ack(event_id)
            return True

        if cmd_type == ALERT_TYPE:
            log.warning(
                "ALERT_OPERATOR | device=%s | reason=%s",
                device_uid, cmd.get("reason", "-"),
            )
            self._mark_ack(event_id)
            return True

        if cmd_type in ACTIONABLE_TYPES:
            topic = f"commands/{device_uid}"
            payload = json.dumps({
                "event_id":  event_id,
                "type":      cmd_type,
                "params":    cmd.get("params", {}),
                "reason":    cmd.get("reason", ""),
                "severity":  row.get("severity", "warning"),
                "issued_at": str(row.get("created_at", "")),
            }, ensure_ascii=False)

            mqtt_client = self._get_mqtt_client()
            if mqtt_client is None:
                log.error(
                    "MQTT unavailable - command %s for %s postponed.",
                    cmd_type, device_uid,
                )
                return False

            try:
                result = mqtt_client.publish(topic, payload, qos=1)
                result.wait_for_publish(timeout=5)
                log.info(
                    "Command '%s' sent -> %s (event #%d)",
                    cmd_type, topic, event_id,
                )
            except Exception as exc:
                log.error("MQTT publication error: %s", exc)
                return False
        else:
            log.warning("Unknown command type '%s' in event #%d", cmd_type, event_id)

        self._mark_ack(event_id)
        return True

    def _mark_ack(self, event_id: int) -> None:
        try:
            with self._pg_conn.cursor() as cur:
                cur.execute(
                    "UPDATE event_log SET command_ack = TRUE WHERE id = %s",
                    (event_id,),
                )
        except psycopg2.OperationalError:
            raise
        except Exception as exc:
            log.error("Failed to update command_ack for #%d: %s", event_id, exc)

    def close(self):
        if self._mqtt_client:
            try:
                self._mqtt_client.loop_stop()
                self._mqtt_client.disconnect()
            except Exception:
                pass
