import os

PG_DSN = os.getenv(
    "PG_DSN",
    "host=localhost port=5432 dbname=platform user=platform password=platform123",
)

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# InfluxDB
INFLUX_URL    = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN", "my-super-secret-token")
INFLUX_ORG    = os.getenv("INFLUX_ORG", "platform")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "telemetry")

# Auth0
AUTH0_DOMAIN    = os.getenv("AUTH0_DOMAIN", "")
AUTH0_AUDIENCE  = os.getenv("AUTH0_AUDIENCE", "")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")

# Gemini API Key
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
