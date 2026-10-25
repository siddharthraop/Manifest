"""Two end-to-end `manifest migrate` runs on the real corpus, shared by the story and metric tests:
`baseline` (no mapping rules) and `migrated` (with the fixture rules)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from manifest.tools.convert import ConvertError, find_dita
from manifest.tools.links import LinkError, find_lychee

ROOT = Path(__file__).resolve().parent.parent
TPU_MAP = ROOT / "corpus" / "tpu" / "tpu.ditamap"


def _have_toolchain() -> bool:
    try:
        find_dita()
        find_lychee()
    except (ConvertError, LinkError):
        return False
    sphinx = ("sphinx", "myst_parser", "sphinx_substitution_extensions")
    return TPU_MAP.exists() and all(importlib.util.find_spec(m) for m in sphinx)


needs_toolchain = pytest.mark.skipif(
    not _have_toolchain(), reason="needs DITA-OT, lychee, [sphinx] and the corpus"
)


FIXTURE_RULES = Path(__file__).parent / "fixtures" / "rules"


@pytest.fixture(scope="session")
def baseline(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Path]:
    """(report, build folder) with no mapping rules: the specialized slice-planning topic fails."""
    from manifest.pipeline import migrate

    out = tmp_path_factory.mktemp("baseline")
    return migrate(TPU_MAP, out), out


@pytest.fixture(scope="session")
def migrated(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Path]:
    """(report, build folder) with the fixture rules for <choicetable> and <hazardstatement>."""
    from manifest.pipeline import migrate
    from manifest.rules import RuleSet, load_rules

    out = tmp_path_factory.mktemp("migrate")
    return migrate(TPU_MAP, out, rules=RuleSet(load_rules(FIXTURE_RULES))), out


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
