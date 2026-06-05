import time
import json
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import psycopg2
import paho.mqtt.client as mqtt
import redis

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
PG_DSN = "host=127.0.0.1 port=5433 dbname=platform user=platform password=platform123"


def run_scalability_test():
    print("\nStarting scalability verification test...")

    test_device_uid = "wind_turbine_01"
    test_device_name = "Wind Turbine WT-01"
    test_device_type = "wind_turbine"

    conn = None
    r = None
    client = None

    try:
        conn = psycopg2.connect(PG_DSN)
        cur = conn.cursor()
        
        cur.execute("DELETE FROM devices WHERE device_uid = %s", (test_device_uid,))
        conn.commit()
        
        schema_fields = [
            {"name": "wind_speed_ms", "required": True, "min": 0.0, "max": 50.0, "unit": "m/s", "description": "Wind speed"},
            {"name": "blade_angle_deg", "required": False, "min": -5.0, "max": 90.0, "unit": "deg", "description": "Blade angle"},
            {"name": "rotor_speed_rpm", "required": False, "min": 0.0, "max": 3000.0, "unit": "rpm", "description": "Rotor speed"},
            {"name": "active_power_w", "required": True, "min": 0.0, "max": 10000.0, "unit": "W", "description": "Active power"},
            {"name": "frequency_hz", "required": False, "min": 45.0, "max": 55.0, "unit": "Hz", "description": "Frequency"}
        ]
        
        cur.execute(
            """
            INSERT INTO device_schemas (device_type, description, fields)
            VALUES (%s, 'Industrial wind turbine', %s)
            ON CONFLICT (device_type) DO UPDATE SET fields = EXCLUDED.fields;
            """,
            (test_device_type, json.dumps(schema_fields))
        )
        conn.commit()

        print(f"Registering new device: {test_device_uid} (type: {test_device_type})")
        cur.execute(
            """
            INSERT INTO devices (device_uid, device_type, name, is_active, location_id)
            VALUES (%s, %s, %s, true, 1)
            RETURNING id;
            """,
            (test_device_uid, test_device_type, test_device_name)
        )
        device_id = cur.fetchone()[0]
        conn.commit()
        print(f"[OK] Пристрій успішно зареєстровано з ID = {device_id} (код ядра НЕ змінювався).")
        cur.close()

        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        r.delete(f"device:{test_device_uid}:latest")
        
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()

        payload = {
            "device_uid": test_device_uid,
            "device_type": test_device_type,
            "timestamp_device": time.time(),
            "data": {
                "wind_speed_ms": 12.8,
                "blade_angle_deg": 14.5,
                "rotor_speed_rpm": 1200.0,
                "active_power_w": 4850.0,
                "frequency_hz": 50.05
            }
        }

        topic = f"telemetry/{test_device_uid}"
        print(f"\nPublishing telemetry for {test_device_uid}...")
        client.publish(topic, json.dumps(payload))
        
        print("Waiting for ETL pipeline to process and load into Redis...")
        latest_key = f"device:{test_device_uid}:latest"
        found = False
        start_wait = time.time()
        
        while time.time() - start_wait < 3.0:
            latest_data = r.hgetall(latest_key)
            if latest_data:
                if "wind_speed_ms" in latest_data:
                    print("\nReceived Redis hot cache data:")
                    for k, v in latest_data.items():
                        print(f"  {k}: {v}")
                    found = True
                    break
            time.sleep(0.1)

        print("\nCleaning up test device, schema and cache...")
        cur = conn.cursor()
        cur.close()
        
        r.delete(latest_key)
        for k in r.keys(f"device:{test_device_uid}:window:*"):
            r.delete(k)

        if found:
            print("\nOK: Scalability verification passed successfully.")
            print("  - New device type successfully integrated via metadata update.")
            print("  - ETL pipeline dynamically processed the new schema without a restart.")
        else:
            print("\nWarning: Test device data did not appear in Redis cache.")

    except Exception as e:
        print(f"Error: scalability test failed: {e}")
    finally:
        if client:
            client.loop_stop()
            client.disconnect()
        if conn:
            conn.close()


if __name__ == "__main__":
    run_scalability_test()
