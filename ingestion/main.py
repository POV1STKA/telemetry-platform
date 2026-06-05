import json
import logging
import os
import signal
import sys
import time

import paho.mqtt.client as mqtt
import pika
import pika.exceptions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INGEST] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MQTT_HOST  = os.getenv("MQTT_HOST",  "mosquitto")
MQTT_PORT  = int(os.getenv("MQTT_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "telemetry/#")

RABBIT_HOST = os.getenv("RABBIT_HOST", "rabbitmq")
RABBIT_PORT = int(os.getenv("RABBIT_PORT", 5672))
RABBIT_USER = os.getenv("RABBIT_USER", "platform")
RABBIT_PASS = os.getenv("RABBIT_PASS", "platform123")

EXCHANGE_NAME = "telemetry"
QUEUE_NAME    = "telemetry.raw"
DLQ_NAME      = "telemetry.dead"

_rabbit_channel = None
_rabbit_conn    = None


def _connect_rabbit() -> None:
    global _rabbit_conn, _rabbit_channel

    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    params = pika.ConnectionParameters(
        host=RABBIT_HOST,
        port=RABBIT_PORT,
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=300,
    )
    _rabbit_conn    = pika.BlockingConnection(params)
    _rabbit_channel = _rabbit_conn.channel()

    _rabbit_channel.exchange_declare(
        exchange="telemetry.dlx",
        exchange_type="fanout",
        durable=True,
    )
    _rabbit_channel.queue_declare(
        queue=DLQ_NAME,
        durable=True,
    )
    _rabbit_channel.queue_bind(queue=DLQ_NAME, exchange="telemetry.dlx")

    _rabbit_channel.exchange_declare(
        exchange=EXCHANGE_NAME,
        exchange_type="direct",
        durable=True,
    )

    _rabbit_channel.queue_declare(
        queue=QUEUE_NAME,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "telemetry.dlx",
            "x-message-ttl": 300_000,
        },
    )
    _rabbit_channel.queue_bind(
        queue=QUEUE_NAME,
        exchange=EXCHANGE_NAME,
        routing_key="raw",
    )

    log.info("RabbitMQ connected. Exchange=%s, Queue=%s", EXCHANGE_NAME, QUEUE_NAME)


def _publish_to_rabbit(topic: str, payload: bytes) -> None:
    global _rabbit_channel, _rabbit_conn

    headers = {
        "mqtt_topic":      topic,
        "ingested_at":     int(time.time()),
        "source_protocol": "mqtt",
    }

    properties = pika.BasicProperties(
        delivery_mode=2,
        content_type="application/json",
        headers=headers,
    )

    try:
        _rabbit_channel.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key="raw",
            body=payload,
            properties=properties,
            mandatory=True,
        )
    except (pika.exceptions.AMQPConnectionError,
            pika.exceptions.ChannelClosedByBroker) as e:
        log.warning("RabbitMQ connection lost: %s. Reconnecting...", e)
        _connect_rabbit()

        _rabbit_channel.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key="raw",
            body=payload,
            properties=properties,
        )


_stats = {"received": 0, "published": 0, "errors": 0}


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        client.subscribe(MQTT_TOPIC, qos=1)
        log.info("MQTT connected -> subscribed to: %s", MQTT_TOPIC)
    else:
        log.error("MQTT connection error: %s", reason_code)


def on_message(client, userdata, msg):
    _stats["received"] += 1

    if "simulator/status" in msg.topic:
        return

    try:
        _publish_to_rabbit(msg.topic, msg.payload)
        _stats["published"] += 1
        log.debug("-> RabbitMQ: %s (%d bytes)", msg.topic, len(msg.payload))
    except Exception as e:
        _stats["errors"] += 1
        log.error("Failed to publish %s: %s", msg.topic, e)


def _shutdown(sig, frame):
    log.info("Stopping. Statistics: %s", _stats)
    if _rabbit_conn and _rabbit_conn.is_open:
        _rabbit_conn.close()
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("Ingestion service started | MQTT=%s | RabbitMQ=%s", MQTT_HOST, RABBIT_HOST)

    while True:
        try:
            _connect_rabbit()
            break
        except Exception as e:
            log.warning("RabbitMQ is unavailable: %s. Retrying in 5 seconds...", e)
            time.sleep(5)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="ingestion_service",
    )
    client.on_connect = on_connect
    client.on_message = on_message

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            break
        except Exception as e:
            log.warning("MQTT is unavailable: %s. Retrying in 5 seconds...", e)
            time.sleep(5)

    log.info("Ingestion service ready. Forwarding MQTT -> RabbitMQ...")
    client.loop_forever()


if __name__ == "__main__":
    main()
