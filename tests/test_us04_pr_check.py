"""US-04: as a software engineer, I want a check on my pull request that tells me if I broke a link, an
include or a key, so that I can fix docs without a writer.
Accepted when: the check runs on every pull request; each error gives the file, the line and the fix."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import ROOT

from manifest.cli import main
from manifest.tools.lint import lint_map

CORPUS = ROOT / "corpus"


@pytest.fixture
def broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the TPU map with one broken key, one broken conref and one broken link."""
    shutil.copytree(CORPUS / "tpu", tmp_path / "tpu")
    topic = tmp_path / "tpu" / "topics" / "tpu-create-vm.dita"
    text = topic.read_text()
    first_key = text.split('keyref="', 1)[1].split('"', 1)[0]
    text = text.replace(f'keyref="{first_key}"', 'keyref="no-such-key"', 1)
    text = text.replace("</shortdesc>", '</shortdesc>\n  <p conref="../reuse/missing.dita#nope/nope"/>'
                        '<p><xref href="no-such-topic.dita"/></p>', 1)  # fmt: skip
    topic.write_text(text)
    monkeypatch.chdir(tmp_path)
    return Path("tpu/tpu.ditamap")


def test_a_clean_map_passes() -> None:
    assert lint_map(CORPUS / "tpu" / "tpu.ditamap") == []
    assert main(["lint", str(CORPUS / "tpu" / "tpu.ditamap")]) == 0


def test_each_error_gives_the_file_the_line_and_the_fix(broken: Path) -> None:
    problems = lint_map(broken)
    kinds = sorted(p.kind for p in problems)
    assert {"key", "conref", "link"} <= set(kinds)
    for p in problems:
        assert p.file == "tpu/topics/tpu-create-vm.dita" and isinstance(p.line, int) and p.line > 0
        assert p.fix and p.text().startswith(f"{p.file}:{p.line}: ") and "Fix: " in p.text()
    assert any("no-such-key" in p.message for p in problems)


def test_the_check_annotates_the_pull_request_and_fails_it(
    broken: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["lint", str(broken), "--format", "github"]) == 1
    out = capsys.readouterr().out
    assert "::error file=tpu/topics/tpu-create-vm.dita,line=" in out and "title=MANIFEST key::" in out


def test_the_action_runs_the_check_on_pull_requests() -> None:
    action = (ROOT / "action" / "action.yml").read_text()
    assert "manifest lint" in action and "--format github" in action
    guide = (ROOT / "help" / "topics" / "t-add-github-action.dita").read_text()
    assert "on: pull_request" in guide and "siddharthraop/Manifest/action@" in guide
