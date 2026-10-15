from __future__ import annotations

import socket
from contextlib import contextmanager

from .config import MySQLConfig, RedshiftConfig


@contextmanager
def mysql_connection(config: MySQLConfig):
    import pymysql

    connection = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        cursorclass=pymysql.cursors.DictCursor,
        read_timeout=300,
        write_timeout=300,
    )
    # Enable TCP keepalive so the SSH tunnel channel stays alive during long queries
    try:
        sock = connection.socket
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)
    except (AttributeError, OSError):
        pass
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def redshift_connection(config: RedshiftConfig):
    import trino.dbapi
    from trino.auth import BasicAuthentication

    connection = trino.dbapi.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        catalog=config.database,
        http_scheme="https",
        auth=BasicAuthentication(config.user, config.password),
        request_timeout=60,
    )
    try:
        yield connection
    finally:
        connection.close()
