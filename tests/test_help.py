"""The help gate (handoff §6): every help ID in the console resolves to a help topic, every topic is
complete, and the API reference matches the API. A screen without help fails the build."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys

from conftest import ROOT
from lxml import etree

HELP = ROOT / "help"
spec = importlib.util.spec_from_file_location("build_csh", HELP / "tools" / "build_csh.py")
assert spec and spec.loader
build_csh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_csh)


def console_help_ids() -> set[str]:
    ids: set[str] = set()
    for f in ("index.html", "app.js"):
        text = (ROOT / "console" / f).read_text()
        ids |= set(re.findall(r'data-help-id="([a-z-]+)"', text))
        ids |= set(re.findall(r'helpId = "([a-z-]+)"', text))
        ids |= set(re.findall(r'helpBtn\("([a-z-]+)"', text))
    return ids


def test_every_help_id_in_the_console_has_a_topic() -> None:
    csh = build_csh.build()
    ids = console_help_ids()
    assert len(ids) >= 10, ids
    assert not ids - set(csh), f"help IDs without a topic: {sorted(ids - set(csh))}"
    for entry in csh.values():
        assert entry["title"] and entry["shortdesc"] and entry["href"].startswith("topics/")


def test_every_screen_has_help() -> None:
    screens = {"overview", "maps", "run-detail", "review-queue", "rules", "settings"}
    assert screens <= console_help_ids() <= set(build_csh.build()) | screens


def test_every_topic_has_a_short_description_and_alt_text() -> None:
    parser = etree.XMLParser(load_dtd=False, no_network=True)
    for f in sorted((HELP / "topics").glob("*.dita")):
        root = etree.parse(str(f), parser).getroot()
        sd = root.find("shortdesc")
        assert sd is not None and "".join(sd.itertext()).strip() or len(sd) > 0, (
            f"{f.name}: no short description"
        )
        for img in root.iter("image"):
            assert img.find("alt") is not None, f"{f.name}: image without alt text"


def test_every_topic_is_in_the_map() -> None:
    in_map = {p.name for p in build_csh.topics()}
    on_disk = {p.name for p in (HELP / "topics").glob("*.dita")}
    assert on_disk == in_map, on_disk ^ in_map


def test_the_api_reference_matches_the_api() -> None:
    r = subprocess.run([sys.executable, str(HELP / "tools" / "openapi2dita.py"), "--check"], cwd=ROOT,
                       capture_output=True, text=True)  # fmt: skip
    assert r.returncode == 0, r.stderr
