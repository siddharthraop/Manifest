"""Design doc §7 success metrics, measured on the TPU sample map. The MVP succeeds if these are met."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import needs_toolchain

TARGETS = {
    "Unreported losses": "0",
    "Topics checked": "100%",
    "Reuse kept": "100%",
    "IDs kept": "100%",
    "Broken links": "0",
}


@needs_toolchain
@pytest.mark.parametrize(("metric", "target"), TARGETS.items())
def test_mvp_metric_is_met(migrated: tuple[dict[str, Any], Path], metric: str, target: str) -> None:
    report, _ = migrated
    measured = next(m for m in report["metrics"] if m["metric"] == metric)
    assert measured["measured"] == target, measured


@needs_toolchain
def test_tracked_metrics_are_reported(migrated: tuple[dict[str, Any], Path]) -> None:
    report, _ = migrated
    names = [m["metric"] for m in report["metrics"]]
    assert names[-2:] == ["Topics passed without a writer", "Fixes approved"]
    assert (
        next(m for m in report["metrics"] if m["metric"] == "Topics passed without a writer")["measured"]
        == "22/23"
    )
