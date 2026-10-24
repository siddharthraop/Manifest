"""One end-to-end `manifest migrate` run on the real corpus, shared by the story and metric tests."""

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


@pytest.fixture(scope="session")
def migrated(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, Any], Path]:
    """(report, build folder) for one full run on corpus/tpu/tpu.ditamap."""
    from manifest.pipeline import migrate

    out = tmp_path_factory.mktemp("migrate")
    report = migrate(TPU_MAP, out)
    return report, out


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
