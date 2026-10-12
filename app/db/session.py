from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine
    if url is not None:
        return create_engine(url, future=True)
    if _engine is None:
        _engine = create_engine(get_settings().database_url, future=True, pool_pre_ping=True)
    return _engine


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    with factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
