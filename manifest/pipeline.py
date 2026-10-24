"""`manifest migrate MAP`: the five tools in order, then the report.

    build/inventory.json      tool 1, the "before" picture
    build/convert/            tool 2, DITA-OT Markdown with placeholders, ledger.json
    build/myst/               tool 3, the migrated Sphinx project (the deliverable)
    build/check/              tool 4, DITA-OT HTML5 and Sphinx for every build, check.json
    build/links/              tool 5, links.json, redirects.json
    build/report/             report.json, report.html

Every step is deterministic. The agent (build step 3) will sit between the checks and the
report, proposing fixes for topics that fail; this pipeline is what it calls.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from typing import Any

from manifest.report import build_report, write_report
from manifest.rules import RuleSet
from manifest.tools.check import check_topics
from manifest.tools.convert import convert_map, dita_ot_version, find_dita
from manifest.tools.inventory import inventory_map
from manifest.tools.links import check_links
from manifest.tools.restore import restore_reuse


def _version(dist: str) -> str:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "not installed"


def migrate(
    map_path: Path,
    out: Path,
    dita: str | None = None,
    lychee: str | None = None,
    rules: RuleSet | None = None,
) -> dict[str, Any]:
    """rules: the approved mapping rules to apply (default: none)."""
    dita_cmd = find_dita(dita)
    inv = inventory_map(map_path)
    inv.write(out / "inventory.json")
    conv = convert_map(map_path, out / "convert", dita_cmd, rules)
    if not conv.ok:
        raise RuntimeError("convert_map lost placeholders; see convert/convert.json")
    restore_reuse(map_path, out / "convert", out / "myst")
    check_topics(map_path, out / "myst", out / "check", dita_cmd)
    lnk = check_links(out / "check", out / "links", lychee)

    def load(p: Path) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return data

    versions = {
        "DITA-OT": dita_ot_version(dita_cmd),
        "Sphinx": _version("sphinx"),
        "MyST-Parser": _version("myst-parser"),
        "lychee": lnk.lychee,
    }
    report = build_report(
        inv.to_dict(),
        load(out / "convert" / "convert.json"),
        load(out / "myst" / "_manifest" / "restore.json"),
        load(out / "check" / "check.json"),
        load(out / "links" / "links.json"),
        versions,
    )
    write_report(report, out / "report")
    return report
