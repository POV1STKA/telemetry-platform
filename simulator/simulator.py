import json
import math
import os
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
PUBLISH_INTERVAL = float(os.getenv("PUBLISH_INTERVAL_SEC", 5))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SIM] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


@dataclass
class DeviceState:
    soc: float = 75.0
    temp: float = 25.0
    voltage: float = 52.0
    load_w: float = 800.0
    pv_w: float = 0.0
    tick: int = 0
    anomaly_mode: str = "normal"
    
    b1_soc: float = 75.0
    b1_temp: float = 25.0
    b1_voltage: float = 52.0
    inv_temp: float = 25.0
    
    target_load_w: float = 800.0


def simulate_solar(tick: int) -> float:
    day_fraction = (tick % 360) / 360
    angle = math.pi * day_fraction
    raw = math.sin(angle)
    if raw < 0:
        return 0.0
    val = raw * 2500 + random.uniform(-1.0, 1.0)
    return round(max(0.0, val), 1)


def update_state(s: DeviceState) -> DeviceState:
    s.tick += 1
    s.pv_w = simulate_solar(s.tick)

    if s.target_load_w > 0.0:
        s.load_w = round(s.target_load_w + random.uniform(-5.0, 5.0), 1)
    else:
        s.load_w = 0.0

    net_w = s.pv_w - s.load_w
    delta_soc = (net_w / (200 * 52)) * (PUBLISH_INTERVAL / 3600) * 100
    s.soc = max(50.0, min(100.0, round(s.soc + delta_soc, 2)))
    s.voltage = round(44.0 + (s.soc / 100) * 14.4 + random.uniform(-0.1, 0.1), 2)
    
    target_temp = 25.0 + (s.load_w / 3000) * 20
    s.temp = round(s.temp + (target_temp - s.temp) * 0.05 + random.uniform(-0.1, 0.1), 1)

    if s.anomaly_mode == "low_soc":
        s.b1_soc = max(4.0, round(s.b1_soc - 12.0, 2))
        s.b1_voltage = round(44.0 + (s.b1_soc / 100) * 10.4 + random.uniform(-0.1, 0.1), 2)
    elif s.anomaly_mode == "combined":
        s.b1_soc = max(4.0, round(s.b1_soc - 12.0, 2))
        s.b1_voltage = round(44.0 + (s.b1_soc / 100) * 10.4 + random.uniform(-0.1, 0.1), 2)
        s.b1_temp = min(63.0, round(s.b1_temp + 6.0, 1))
    elif s.anomaly_mode == "overtemp":
        s.inv_temp = min(63.0, round(s.inv_temp + 6.0, 1))
    else:
        if s.load_w == 0.0:
            if s.pv_w > 0:
                s.b1_soc = min(75.0, round(s.b1_soc + (s.pv_w / (200 * 52)) * 1000, 2))
            s.b1_temp = round(s.b1_temp + (25.0 - s.b1_temp) * 0.1, 1)
            s.inv_temp = round(s.inv_temp + (25.0 - s.inv_temp) * 0.1, 1)
            s.b1_voltage = round(44.0 + (s.b1_soc / 100) * 10.4 + random.uniform(-0.1, 0.1), 2)
        else:
            s.b1_soc = round(s.b1_soc + (s.soc - s.b1_soc) * 0.1, 2)
            s.b1_temp = round(s.b1_temp + (s.temp - s.b1_temp) * 0.1, 1)
            s.b1_voltage = round(s.b1_voltage + (s.voltage - s.b1_voltage) * 0.1, 2)
            s.inv_temp = round(s.inv_temp + (s.temp - s.inv_temp) * 0.1, 1)

    return s


def inject_anomaly(s: DeviceState, mode: str) -> DeviceState:
    if mode == "low_soc":
        s.soc = round(random.uniform(8, 15), 2)
        s.voltage = round(44.5 + random.uniform(-0.5, 0.5), 2)
        log.warning("Low SoC anomaly: %.1f%%", s.soc)

    elif mode == "overtemp":
        s.temp = round(random.uniform(52, 61), 1)
        log.warning("Overtemperature anomaly: %.1f°C", s.temp)

    elif mode == "combined":
        s.soc = round(random.uniform(12, 18), 2)
        s.temp = round(random.uniform(48, 55), 1)
        s.voltage = round(44.2 + random.uniform(-0.3, 0.3), 2)
        s.load_w = round(2600 + random.uniform(0, 400), 1)
        log.warning(
            "Combined anomaly: SoC=%.1f%% Temp=%.1f°C V=%.2fV Load=%.0fW",
            s.soc, s.temp, s.voltage, s.load_w
        )

    elif mode == "sensor_spike":
        s.voltage = round(random.uniform(80, 99), 2)
        log.warning("Sensor spike anomaly: V=%.2f", s.voltage)

    return s


def make_inverter_packet(uid: str, s: DeviceState) -> dict:
    volt_val = s.voltage
    temp_val = s.inv_temp
    load_val = s.load_w
        
    return {
        "device_uid": uid,
        "device_type": "inverter",
        "timestamp_device": time.time(),
        "data": {
            "dc_voltage_v":      volt_val,
            "dc_current_a":      round(load_val / max(volt_val, 1), 2),
            "ac_output_power_w": load_val,
            "ac_output_v":       round(220 + random.uniform(-1.0, 1.0), 1),
            "temperature_c":     temp_val,
            "state":             "inverting" if s.b1_soc > 5 else "low_battery_shutdown",
        }
    }


def make_charge_controller_packet(uid: str, s: DeviceState) -> dict:
    return {
        "device_uid": uid,
        "device_type": "charge_controller",
        "timestamp_device": time.time(),
        "data": {
            "pv_power_w":       s.pv_w,
            "pv_voltage_v":     round(s.pv_w / 8.5, 1) if s.pv_w > 0 else 0.0,
            "battery_voltage_v": s.voltage,
            "charge_current_a": round(s.pv_w / max(s.voltage, 1), 2),
            "temperature_c":    round(s.temp - 3 + random.uniform(-0.3, 0.3), 1),
            "charge_state":     _charge_state(s),
        }
    }


def make_battery_packet(uid: str, s: DeviceState, index: int = 0) -> dict:
    offset = index * 0.5
    
    soc_val = s.b1_soc if index == 0 else (s.soc - offset)
    volt_val = s.b1_voltage if index == 0 else (s.voltage - offset * 0.1)
    temp_val = s.b1_temp if index == 0 else (s.temp - 2 + random.uniform(-0.3, 0.3))
            
    return {
        "device_uid": uid,
        "device_type": "battery",
        "timestamp_device": time.time(),
        "data": {
            "soc_pct":      round(soc_val, 2),
            "voltage_v":    round(volt_val, 2),
            "current_a":    round((s.pv_w - s.load_w) / max(volt_val, 1), 2),
            "temperature_c": round(temp_val, 1),
            "cycles":       142 + index * 7,
            "health_pct":   round(98.5 - index * 0.5, 1),
        }
    }


def make_sensor_packet(uid: str, s: DeviceState) -> dict:
    volt_val = round(220 + random.uniform(-1.0, 1.0), 1)
    
    if s.anomaly_mode == "sensor_spike":
        volt_val = round(random.uniform(320.0, 350.0), 1)
        
    return {
        "device_uid": uid,
        "device_type": "sensor",
        "timestamp_device": time.time(),
        "data": {
            "temperature_c": round(22.0 + random.uniform(-0.3, 0.3), 1),
            "humidity_pct":  round(55.0 + random.uniform(-1.0, 1.0), 1),
            "ac_mains_v":    volt_val,
            "mains_ok":      True,
        }
    }


def _charge_state(s: DeviceState) -> str:
    if s.pv_w < 10:
        return "idle"
    if s.soc >= 98:
        return "float"
    if s.soc >= 80:
        return "absorption"
    return "bulk"


_state_ref: list = []
_mqtt_client_ref: list = []


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info("Connected to MQTT broker at %s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe("commands/#", qos=1)
        log.info("Subscribed to 'commands/#' topic.")
    else:
        log.error("MQTT connection error, code: %s", reason_code)


def on_publish(client, userdata, mid, reason_code, properties):
    pass


def on_message(client, userdata, msg):
    topic = msg.topic
    if topic.endswith("/ack"):
        return

    try:
        cmd = json.loads(msg.payload.decode())
    except Exception as exc:
        log.warning("Failed to parse command from '%s': %s", topic, exc)
        return

    parts = topic.split("/")
    device_uid = parts[1] if len(parts) >= 2 else "unknown"
    cmd_type   = cmd.get("type", "none")
    event_id   = cmd.get("event_id", "?")

    log.info(
        "Received command '%s' for '%s' (event #%s)",
        cmd_type, device_uid, event_id,
    )

    state = _state_ref[0] if _state_ref else None
    if state is not None:
        _apply_command(state, cmd_type, cmd.get("params", {}))

    ack_topic   = f"commands/{device_uid}/ack"
    ack_payload = json.dumps({
        "event_id":   event_id,
        "device_uid": device_uid,
        "cmd_type":   cmd_type,
        "status":     "executed",
        "ts":         time.time(),
    }, ensure_ascii=False)

    client.publish(ack_topic, ack_payload, qos=1)
    log.info("ACK sent: %s", ack_topic)


def _apply_command(state: DeviceState, cmd_type: str, params: dict) -> None:
    if cmd_type == "reduce_load":
        reduction = params.get("reduction_pct", 30)
        old_load  = state.load_w
        state.load_w = round(state.load_w * (1 - reduction / 100), 1)
        log.warning(
            "reduce_load: load %s -> %s W (-%d%%)",
            old_load, state.load_w, reduction,
        )

    elif cmd_type == "disconnect_load":
        state.load_w = 0.0
        log.warning("disconnect_load: load turned off (0 W)")

    elif cmd_type == "charge_limit":
        max_pct = params.get("max_soc_pct", 80)
        if state.soc > max_pct:
            state.soc = float(max_pct)
        log.warning(
            "charge_limit: SoC limited to %d%%", max_pct
        )

    elif cmd_type == "alert_operator":
        log.warning(
            "alert_operator: %s", params.get("message", "Operator attention required")
        )

    else:
        log.warning("Unknown command type: '%s'", cmd_type)


def main():
    state = DeviceState()
    _state_ref.append(state)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="device_simulator",
    )
    client.on_connect = on_connect
    client.on_publish  = on_publish
    client.on_message  = on_message
    _mqtt_client_ref.append(client)

    client.will_set(
        "telemetry/simulator/status",
        payload=json.dumps({"status": "offline"}),
        qos=1,
        retain=True,
    )

    log.info("Connecting to MQTT broker at %s:%s ...", MQTT_HOST, MQTT_PORT)
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            log.warning("Could not connect: %s. Retrying in 5 seconds...", e)
            time.sleep(5)

    client.loop_start()

    client.publish(
        "telemetry/simulator/status",
        json.dumps({"status": "online"}),
        qos=1, retain=True,
    )

    cycle = 0
    log.info("Simulator started. Interval: %ss. Ctrl+C to stop.", PUBLISH_INTERVAL)

    while True:
        cycle += 1
        state = update_state(state)
        if _state_ref:
            _state_ref[0] = state

        scenario_idx = (cycle // 12) % 14
        scenario_names = [
            "normal", "normal", 
            "low_soc", 
            "normal", "normal", 
            "overtemp", 
            "normal", "normal", 
            "combined", 
            "normal", "normal", 
            "sensor_spike", 
            "normal", "normal"
        ]
        scenario = scenario_names[scenario_idx]

        tick_within_scenario = cycle % 12
        if tick_within_scenario < 6 and scenario != "normal":
            state.anomaly_mode = scenario
        else:
            state.anomaly_mode = "normal"

        if scenario == "normal" and state.target_load_w < 800.0:
            state.target_load_w = 800.0
            state.load_w = 800.0
            log.warning("Auto-recovery: load connected after anomaly scenario ended.")

        packets = [
            ("telemetry/inverter_01",          make_inverter_packet("inverter_01", state)),
            ("telemetry/charge_controller_01", make_charge_controller_packet("charge_controller_01", state)),
            ("telemetry/battery_01",           make_battery_packet("battery_01", state, 0)),
            ("telemetry/battery_02",           make_battery_packet("battery_02", state, 1)),
            ("telemetry/sensor_env_01",        make_sensor_packet("sensor_env_01", state)),
        ]

        for topic, payload in packets:
            result = client.publish(
                topic,
                json.dumps(payload),
                qos=1,
            )
            result.wait_for_publish(timeout=3)

        log.info(
            "Cycle %04d | SoC=%.1f%% V=%.2fV T=%.1f°C PV=%.0fW Load=%.0fW | scenario=%s",
            cycle, state.soc, state.voltage, state.temp,
            state.pv_w, state.load_w,
            scenario,
        )

        time.sleep(PUBLISH_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Simulator stopped.")
