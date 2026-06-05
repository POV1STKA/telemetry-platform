import os

# Gemini Cloud API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_ANOMALY_LIMIT = int(os.getenv("REDIS_ANOMALY_LIMIT", "10"))
REDIS_WINDOW_LIMIT = int(os.getenv("REDIS_WINDOW_LIMIT", "10"))

# PostgreSQL
PG_DSN = os.getenv(
    "PG_DSN",
    "host=postgres port=5432 dbname=platform user=platform password=platform123",
)

# Polling
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "10"))
MIN_SEVERITY = os.getenv("MIN_SEVERITY", "warning")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))

# MQTT
MQTT_HOST = os.getenv("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
