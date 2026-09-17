"""Connection pool + query helpers. psycopg 3, plain SQL, no ORM.

The rules are set-based SQL; an ORM would only get in the way.
"""
from contextlib import contextmanager
from typing import Any, Iterable

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

pool = ConnectionPool(
    settings.dsn,
    min_size=1,
    max_size=10,
    open=False,
    kwargs={"options": "-c search_path=sg,public -c client_encoding=UTF8"},
)


def open_pool() -> None:
    pool.open()
    pool.wait(timeout=15)


def close_pool() -> None:
    pool.close()


@contextmanager
def conn():
    with pool.connection() as c:
        c.row_factory = dict_row
        yield c


def fetch_all(sql: str, params: Iterable[Any] | dict | None = None) -> list[dict]:
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: Iterable[Any] | dict | None = None) -> dict | None:
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: Iterable[Any] | dict | None = None) -> int:
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount
