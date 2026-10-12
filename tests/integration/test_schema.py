from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Base, Group, SourceMessage


def test_migration_matches_models(engine: Engine) -> None:
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())


def test_logical_key_unique(engine: Engine) -> None:
    with Session(engine) as s:
        g = Group(name="g1")
        s.add(g)
        s.commit()
        now = datetime.now(UTC)
        s.add(SourceMessage(group_id=g.id, logical_key="k", sent_at=now))
        s.commit()
        s.add(SourceMessage(group_id=g.id, logical_key="k", sent_at=now))
        with pytest.raises(IntegrityError):
            s.commit()
