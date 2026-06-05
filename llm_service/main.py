import logging
import signal
import sys
import time

import psycopg2
import redis as redis_lib

import config
from event_processor import process_pending_events
from google_client import check_availability as google_available

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [LLM] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_running = True


def _shutdown(sig, frame):
    global _running
    log.info("Stopping LLM service (signal %s)...", sig)
    _running = False


def _connect_postgres():
    while _running:
        try:
            conn = psycopg2.connect(config.PG_DSN)
            conn.autocommit = True
            log.info("PostgreSQL connected.")
            return conn
        except Exception as e:
            log.warning("PostgreSQL unavailable: %s. Retrying in 5 seconds...", e)
            for _ in range(5):
                if not _running:
                    return None
                time.sleep(1)
    return None


def _connect_redis():
    while _running:
        try:
            r = redis_lib.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                decode_responses=True,
            )
            r.ping()
            log.info("Redis connected (%s:%s).", config.REDIS_HOST, config.REDIS_PORT)
            return r
        except Exception as e:
            log.warning("Redis unavailable: %s. Retrying in 5 seconds...", e)
            for _ in range(5):
                if not _running:
                    return None
                time.sleep(1)
    return None


def _print_startup_info():
    log.info("LLM Service - Telemetry Anomaly Analyzer (Gemini Cloud)")
    log.info("Engine: Google Gemini REST API")
    log.info("Model: gemini-1.5-flash")
    log.info("Redis: %s:%s", config.REDIS_HOST, config.REDIS_PORT)
    log.info("MQTT: %s:%d", config.MQTT_HOST, config.MQTT_PORT)
    log.info("Interval: %ds", config.POLL_INTERVAL_SEC)
    log.info("Min severity: %s", config.MIN_SEVERITY)


def main():
    global _running

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    _print_startup_info()

    pg_conn      = _connect_postgres()
    redis_client = _connect_redis()
    if pg_conn is None or redis_client is None:
        log.info("Shutdown requested during database connection. Exiting.")
        sys.exit(0)

    if google_available():
        log.info("Integration with Google Gemini API is ready.")
    else:
        log.warning("Google Gemini API is unavailable (no key). Fallback to rule-based mode activated.")

    from command_dispatcher import CommandDispatcher
    dispatcher = CommandDispatcher(
        pg_conn   = pg_conn,
        mqtt_host = config.MQTT_HOST,
        mqtt_port = config.MQTT_PORT,
    )
    log.info("CommandDispatcher is ready (MQTT %s:%d).", config.MQTT_HOST, config.MQTT_PORT)

    log.info("LLM service is ready. Polling every %d seconds...", config.POLL_INTERVAL_SEC)

    total_processed  = 0
    total_dispatched = 0

    while _running:
        try:
            n = process_pending_events(pg_conn, redis_client)
            if n > 0:
                total_processed += n
                log.info("Analyzed %d events (total: %d).", n, total_processed)

            d = dispatcher.dispatch_pending()
            if d > 0:
                total_dispatched += d
                log.info("Dispatched %d commands (total: %d).", d, total_dispatched)

        except psycopg2.OperationalError as e:
            log.error("PostgreSQL connection lost: %s", e)
            try:
                pg_conn.close()
            except Exception:
                pass
            pg_conn = _connect_postgres()
            if pg_conn is None:
                break
            dispatcher._pg_conn = pg_conn

        except Exception as e:
            log.error("Unexpected error: %s", e, exc_info=True)

        for _ in range(config.POLL_INTERVAL_SEC):
            if not _running:
                break
            time.sleep(1)

    dispatcher.close()
    log.info("LLM service stopped. Processed %d events, dispatched %d commands.", total_processed, total_dispatched)
    sys.exit(0)


if __name__ == "__main__":
    main()
