"""PostgreSQL connection and transaction utility (psycopg 3)."""
from contextlib import contextmanager
from psycopg import connect
from psycopg.rows import dict_row
from settings import database_url


@contextmanager
def database(url: str | None = None):
    # One request = one short transaction. No in-memory state between processes.
    with connect(url or database_url(), row_factory=dict_row, connect_timeout=15) as conn:
        yield conn
