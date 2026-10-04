"""Real retained evidence must follow execution time, not history-import time."""

from pathlib import Path

from app.core import health
from app.core.models import Check


def test_latest_final_evidence_ignores_later_historical_imports():
    tiles = health.check_rows(Check.query.filter(account__id=1).join("provider"))
    assert len(tiles) == 3
    for tile in tiles:
        assert tile["result"].run.id >= 22
        assert (
            tile["image"]
            == f"/evidence/{tile['result'].run.id}/{Path(tile['result'].screenshot).name}"
        )
        assert Path(tile["result"].screenshot).is_file()
