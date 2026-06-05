import time
import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import psycopg2

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'llm_service')))

try:
    from event_processor import _rule_based_fallback
    print("Successfully imported fallback logic from llm_service.")
except ImportError as e:
    print(f"Error: failed to import event_processor: {e}")
    sys.exit(1)

PG_DSN = "host=127.0.0.1 port=5433 dbname=platform user=platform password=platform123"


def run_fallback_test():
    print("\nStarting fallback and autonomy verification test...")
    
    try:
        conn = psycopg2.connect(PG_DSN)
        print("Local PostgreSQL database is accessible.")
        conn.close()
    except Exception as e:
        print(f"Error: local database is not accessible at 127.0.0.1:5433: {e}")
        return

    simulated_context = {
        "device_uid": "battery_test_fallback",
        "current": {
            "soc_pct": 8.5,
            "temperature_c": 62.3,
            "ac_output_power_w": 3150.0
        },
        "anomalies": [],
        "trends": {}
    }
    
    simulated_event_description = "Critical temperature increase with rapid battery discharge."
    simulated_severity = "critical"

    print("\nRunning fallback analysis...")
    
    start_time = time.time()
    result = _rule_based_fallback(
        context=simulated_context,
        description=simulated_event_description,
        event_severity=simulated_severity
    )
    duration = time.time() - start_time
    duration_ms = duration * 1000

    print(f"Fallback event processing duration: {duration_ms:.4f} ms")
    
    print("\nFormed Fallback Report:")
    print(f"Severity level      : {result.get('severity_assessment')}")
    print(f"Explanation         : \n{result.get('explanation')}")
    print(f"Root cause          : {result.get('root_cause')}")
    print(f"Recommended action  : {result.get('recommended_action')}")
    
    command = result.get('command')
    if command:
        print(f"Generated command   : {command.get('type')}")
        print(f"Command params      : {command.get('params')}")
        print(f"Command reason      : {command.get('reason')}")
    else:
        print("No command generated.")

    if duration < 5.0 and command is not None:
        print(f"\nOK: Fallback and autonomy verification passed.")
        print(f"  - Processing time: {duration_ms:.2f} ms (acceptable: < 5000 ms).")
        print(f"  - Auto-generated emergency control command: {command.get('type')}.")
    else:
        print("\nWarning: Decision logic or execution duration does not meet requirements.")


if __name__ == "__main__":
    run_fallback_test()
