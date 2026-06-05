import logging
import signal
import sys
import time

import pika
import pika.exceptions

import config
from extract import extract, refresh_device_cache
from transform import transform
from load import load, build_load_context
from schema_registry import SchemaRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ETL] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_load_ctx       = None
_pg_conn        = None
_schema_registry = None

_stats = {
    "received":    0,
    "ack":         0,
    "nack_discard": 0,
    "nack_retry":  0,
    "errors":      0,
}


def process_message(channel, method, properties, body):
    delivery_tag = method.delivery_tag
    _stats["received"] += 1

    headers   = properties.headers or {}
    mqtt_topic = headers.get("mqtt_topic", "telemetry/unknown")

    t_start = time.perf_counter()

    # Extract
    try:
        record = extract(mqtt_topic, body, _pg_conn, _schema_registry)
    except Exception as e:
        log.error("Extract critical error: %s", e, exc_info=True)
        channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
        _stats["nack_discard"] += 1
        return

    if record is None:
        channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
        _stats["nack_discard"] += 1
        return

    # Transform
    try:
        metrics = transform(record, _schema_registry)
    except Exception as e:
        log.error("Transform critical error: %s", e, exc_info=True)
        channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
        _stats["nack_discard"] += 1
        return

    # Load
    try:
        write_stats = load(_load_ctx, metrics)
    except Exception as e:
        log.error("Load database error: %s. Requeuing message.", e)
        channel.basic_nack(delivery_tag=delivery_tag, requeue=True)
        _stats["nack_retry"] += 1
        return

    channel.basic_ack(delivery_tag=delivery_tag)
    _stats["ack"] += 1

    t_ms = (time.perf_counter() - t_start) * 1000
    anomaly_count = sum(1 for m in metrics if m.quality_flag != "ok")

    log.info(
        "ACK %-28s | %2d metrics | %d anom | Influx:%d Redis:%d PG:%d | %.1fms",
        record.device_uid, len(metrics), anomaly_count,
        write_stats.get("influx", 0),
        write_stats.get("redis",  0),
        write_stats.get("events", 0),
        t_ms,
    )


def _connect_rabbit():
    credentials = pika.PlainCredentials(config.RABBIT_USER, config.RABBIT_PASS)
    params = pika.ConnectionParameters(
        host=config.RABBIT_HOST,
        port=config.RABBIT_PORT,
        credentials=credentials,
        heartbeat=60,
        blocked_connection_timeout=300,
    )
    conn    = pika.BlockingConnection(params)
    channel = conn.channel()

    channel.basic_qos(prefetch_count=config.RABBIT_PREFETCH)

    channel.queue_declare(
        queue=config.RABBIT_QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": "telemetry.dlx",
            "x-message-ttl": 300_000,
        },
    )

    channel.basic_consume(
        queue=config.RABBIT_QUEUE,
        on_message_callback=process_message,
        auto_ack=False,
    )

    log.info(
        "RabbitMQ connected. Queue=%s, prefetch=%d, auto_ack=False",
        config.RABBIT_QUEUE, config.RABBIT_PREFETCH,
    )
    return conn, channel


def _shutdown(sig, frame):
    log.info("Stopping ETL (signal %s).", sig)
    log.info(
        "Statistics: received=%d ack=%d nack_discard=%d nack_retry=%d errors=%d",
        _stats["received"], _stats["ack"],
        _stats["nack_discard"], _stats["nack_retry"], _stats["errors"],
    )
    sys.exit(0)


def main():
    global _load_ctx, _pg_conn, _schema_registry

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("=" * 58)
    log.info("  ETL Pipeline (RabbitMQ consumer) - starting")
    log.info("  Queue:    %s @ %s:%s", config.RABBIT_QUEUE, config.RABBIT_HOST, config.RABBIT_PORT)
    log.info("  InfluxDB: %s / %s",    config.INFLUX_URL,   config.INFLUX_BUCKET)
    log.info("  Redis:    %s:%s",      config.REDIS_HOST,   config.REDIS_PORT)
    log.info("=" * 58)

    while True:
        try:
            _load_ctx        = build_load_context()
            _pg_conn         = _load_ctx.pg_conn
            _schema_registry = SchemaRegistry(_pg_conn)
            refresh_device_cache(_pg_conn)
            break
        except Exception as e:
            log.warning("Databases unavailable: %s. Retrying in 5 seconds...", e)
            time.sleep(5)

    while True:
        try:
            conn, channel = _connect_rabbit()
            log.info("ETL pipeline ready. Consuming queue '%s'...", config.RABBIT_QUEUE)
            channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as e:
            log.warning("RabbitMQ connection lost: %s. Reconnecting in 5 seconds...", e)
            time.sleep(5)
        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
