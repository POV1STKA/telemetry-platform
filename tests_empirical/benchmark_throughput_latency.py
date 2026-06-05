import time
import json
import random
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import paho.mqtt.client as mqtt
import redis

MQTT_HOST = "127.0.0.1"
MQTT_PORT = 1883
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379


def run_benchmark(num_devices=50):
    print(f"\nStarting benchmark for {num_devices} devices...")
    
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        r.ping()
        print("Connected to Redis successfully.")
    except Exception as e:
        print(f"Error: failed to connect to Redis: {e}")
        return

    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        print("Connected to MQTT broker successfully.")
    except Exception as e:
        print(f"Error: failed to connect to MQTT: {e}")
        return

    latencies = []
    success_count = 0
    total_packets = num_devices

    print(f"Publishing {total_packets} telemetry packets...")

    for i in range(num_devices):
        device_uid = f"benchmark_dev_{i:02d}"
        
        is_inverter = (i % 2 == 0)
        device_type = "inverter" if is_inverter else "battery"
        
        if is_inverter:
            data_dict = {
                "dc_voltage_v": round(random.uniform(45, 55), 2),
                "dc_current_a": round(random.uniform(5, 15), 2),
                "ac_output_power_w": round(random.uniform(1000, 2500), 2),
                "ac_output_v": round(random.uniform(215, 225), 2),
                "temperature_c": round(random.uniform(35, 48), 2)
            }
        else:
            data_dict = {
                "soc_pct": round(random.uniform(80, 100), 2),
                "voltage_v": round(random.uniform(48, 56), 2),
                "current_a": round(random.uniform(-10, 10), 2),
                "temperature_c": round(random.uniform(25, 38), 2),
                "health_pct": 99.0
            }
            
        payload = {
            "device_uid": device_uid,
            "device_type": device_type,
            "timestamp_device": time.time(),
            "data": data_dict
        }
        
        topic = f"telemetry/{device_uid}"
        send_time = time.time()
        
        client.publish(topic, json.dumps(payload))
        
        latest_key = f"device:{device_uid}:latest"
        found = False
        timeout = 2.0
        start_wait = time.time()
        
        while time.time() - start_wait < timeout:
            latest_data = r.hgetall(latest_key)
            if latest_data:
                check_field = "dc_voltage_v" if is_inverter else "voltage_v"
                if check_field in latest_data:
                    latency = time.time() - send_time
                    latencies.append(latency)
                    success_count += 1
                    found = True
                    
                    r.delete(latest_key)
                    r.delete(f"device:{device_uid}:anomalies")
                    for k in r.keys(f"device:{device_uid}:window:*"):
                        r.delete(k)
                    break
            time.sleep(0.01)
            
        if not found:
            print(f"Error: processing timeout for device {device_uid}")

    client.loop_stop()
    client.disconnect()

    if latencies:
        avg_lat = sum(latencies) / len(latencies) * 1000
        min_lat = min(latencies) * 1000
        max_lat = max(latencies) * 1000
        latencies.sort()
        p95_idx = int(len(latencies) * 0.95)
        p95_lat = latencies[p95_idx] * 1000
        loss_rate = ((total_packets - success_count) / total_packets) * 100
        
        print("\nBenchmark results:")
        print(f"Processed packets : {success_count}/{total_packets}")
        print(f"Message loss rate : {loss_rate:.2f}%")
        print(f"Min ETL latency   : {min_lat:.2f} ms")
        print(f"Avg ETL latency   : {avg_lat:.2f} ms")
        print(f"95th percentile   : {p95_lat:.2f} ms")
        print(f"Max ETL latency   : {max_lat:.2f} ms")
        
        if avg_lat < 500 and loss_rate == 0:
            print("OK: Performance requirements met successfully.")
        else:
            print("Warning: Latency or loss rate values exceed acceptable limits.")
    else:
        print("Error: No packets were processed successfully by the pipeline.")


if __name__ == "__main__":
    num_dev = 50
    if len(sys.argv) > 1:
        num_dev = int(sys.argv[1])
    run_benchmark(num_dev)
