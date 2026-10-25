"""US-06 · Priya (reader): a spec value is the same wherever it appears.

Accepted when: every keyed value comes from one substitution · the output has no typed copies of
a keyed value. Values already typed in the source are kept and reported for a writer
(tests/test_restore.py checks the counts match exactly).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from conftest import load, needs_toolchain


@needs_toolchain
def test_every_keyed_value_comes_from_one_substitution(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    keys = load(out / "inventory.json")["keys"]
    subs = load(out / "myst" / "_manifest" / "substitutions.json")
    for key, value in keys.items():
        assert subs[re.sub(r"\W", "_", key)] == value


@needs_toolchain
def test_keys_in_commands_are_substitutions_too(migrated: tuple[dict[str, Any], Path]) -> None:
    _, out = migrated
    task = (out / "myst" / "topics" / "tpu-create-vm.md").read_text()
    assert "--version={{v5e_runtime}}" in task and "v2-alpha-tpuv5-lite" not in task


@needs_toolchain
def test_typed_copies_in_the_source_are_reported(migrated: tuple[dict[str, Any], Path]) -> None:
    report, _ = migrated
    typed = [f for f in report["findings"] if f["kind"] == "possible-typed-copy"]
    assert len(typed) == 19 and all(f["keys"] and f["topic"] for f in typed)
