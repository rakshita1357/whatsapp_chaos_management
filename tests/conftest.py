from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

ADMIN_URL = os.environ.get("WCM_TEST_ADMIN_URL", "postgresql+psycopg://wcm:wcm@127.0.0.1/postgres")
TEST_DB = "wcm_test"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {TEST_DB}"))
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{TEST_DB}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    yield eng
    eng.dispose()
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
