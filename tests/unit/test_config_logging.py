from pathlib import Path

from app.core.config import ROOT, load_yaml
from app.core.logging import mask


def test_seed_configs_load() -> None:
    cfg = ROOT / "config"
    for name in ("groups", "people", "header_aliases", "status_map", "precedence", "reply_weights"):
        assert load_yaml(name, cfg) is not None
    assert load_yaml("reply_weights", cfg)["auto_link_min_score"] == 0.80


def test_missing_seed_raises(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        load_yaml("groups", tmp_path)


def test_phone_numbers_masked() -> None:
    assert "8848991765" not in mask("from +91 88489 91765 ok")
    assert mask("call 918848991765") == "call ***65"
