import os

# MQTT
MQTT_HOST     = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT     = int(os.getenv("MQTT_PORT", 1883))
MQTT_TOPIC    = os.getenv("MQTT_TOPIC", "telemetry/#")
MQTT_CLIENT_ID = "etl_pipeline"

# InfluxDB
INFLUX_URL    = os.getenv("INFLUX_URL",    "http://influxdb:8086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN",  "my-super-secret-token")
INFLUX_ORG    = os.getenv("INFLUX_ORG",   "platform")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "telemetry")

# Redis
REDIS_HOST    = os.getenv("REDIS_HOST", "redis")
REDIS_PORT    = int(os.getenv("REDIS_PORT", 6379))
REDIS_WINDOW  = int(os.getenv("REDIS_WINDOW", 20))
REDIS_TTL_SEC = int(os.getenv("REDIS_TTL_SEC", 600))

# PostgreSQL
PG_DSN = os.getenv(
    "PG_DSN",
    "host=postgres port=5432 dbname=platform user=platform password=platform123"
)

# RabbitMQ
RABBIT_HOST  = os.getenv("RABBIT_HOST",  "rabbitmq")
RABBIT_PORT  = int(os.getenv("RABBIT_PORT", 5672))
RABBIT_USER  = os.getenv("RABBIT_USER",  "platform")
RABBIT_PASS  = os.getenv("RABBIT_PASS",  "platform123")
RABBIT_QUEUE = os.getenv("RABBIT_QUEUE", "telemetry.raw")
RABBIT_PREFETCH = int(os.getenv("RABBIT_PREFETCH", 1))

# Transform
ZSCORE_THRESHOLD = float(os.getenv("ZSCORE_THRESHOLD", "3.5"))
