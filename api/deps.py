import logging
import threading
from typing import Generator

import psycopg2
import psycopg2.extras
import psycopg2.pool
import redis as redis_lib

import config

log = logging.getLogger(__name__)

_pg_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pg_lock = threading.Lock()

_PG_MIN = 2
_PG_MAX = 10

def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pg_pool
    if _pg_pool is not None:
        return _pg_pool
    with _pg_lock:
        if _pg_pool is None:
            _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                _PG_MIN, _PG_MAX, config.PG_DSN
            )
            log.info("PostgreSQL ThreadedConnectionPool initialized (%d-%d connections)", _PG_MIN, _PG_MAX)
    return _pg_pool

def get_pg() -> Generator:
    pool = _get_pool()
    conn = pool.getconn()
    conn.autocommit = True
    try:
        yield conn
    finally:
        pool.putconn(conn)

_redis_client: redis_lib.Redis | None = None
_redis_lock = threading.Lock()

def _get_redis_client() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    with _redis_lock:
        if _redis_client is None:
            _redis_client = redis_lib.Redis(
                host=config.REDIS_HOST,
                port=config.REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=3,
                max_connections=10,
            )
            log.info("Redis singleton client initialized (%s:%d)", config.REDIS_HOST, config.REDIS_PORT)
    return _redis_client

def get_redis() -> Generator:
    yield _get_redis_client()
