"""Command line: `manifest inventory tpu/tpu.ditamap`. More commands arrive with each tool."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from manifest import __version__
from manifest.pipeline import migrate
from manifest.rules import RuleError, RuleSet, load_rules
from manifest.tools.check import CheckError, check_topics
from manifest.tools.convert import ConvertError, convert_map
from manifest.tools.inventory import inventory_map
from manifest.tools.links import LinkError, check_links
from manifest.tools.restore import RestoreError, restore_reuse


def _inventory(args: argparse.Namespace) -> int:
    inv = inventory_map(Path(args.map))
    out = Path(args.out)
    inv.write(out)
    t = inv.totals
    print(
        f"{inv.map}: {t['topics']} topics, {t['elements']} elements, {t['ids']} IDs, "
        f"{t['keyrefs']} key references ({t['keys_defined']} keys), {t['conrefs']} conrefs, "
        f"{t['conditions']} conditions, {t['links']} links, {t['media']} media"
    )
    broken = [(tp.path, c.target) for tp in inv.topics for c in tp.conrefs if not c.resolves]
    undefined = [(tp.path, k.key) for tp in inv.topics for k in tp.keyrefs if not k.defined]
    for path, target in broken:
        print(f"  conref does not resolve: {path} -> {target}")
    for path, key in undefined:
        print(f"  key is not defined: {path} -> {key}")
    print(f"Wrote {out}")
    return 1 if broken or undefined else 0


def _convert(args: argparse.Namespace) -> int:
    try:
        res = convert_map(Path(args.map), Path(args.out), args.dita)
    except ConvertError as exc:
        print(f"error: {exc}")
        return 2
    t = res.totals()
    print(
        f"{res.map}: DITA-OT {res.dita_ot}, {t['files_ok']}/{t['files']} files kept every placeholder "
        f"({t['tokens']} tokens: {t.get('key', 0)} keys, {t.get('conref', 0)} conrefs, "
        f"{t.get('condition', 0)} conditions, {t.get('id', 0)} IDs; {t['includes']} includes)"
    )
    for f in res.files:
        if not f.ok:
            print(f"  FAIL {f.owner}: missing {f.missing} repeated {f.repeated} unexpected {f.unexpected}")
    for e in res.unhandled:
        print(f"  unhandled: {e['owner']} line {e['line']} <{e['element']}>: {e['detail']['reason']}")
    print(f"Wrote {args.out}/markdown, {args.out}/ledger.json, {args.out}/convert.json")
    return 0 if res.ok else 1


def _restore(args: argparse.Namespace) -> int:
    try:
        res = restore_reuse(Path(args.map), Path(args.convert), Path(args.out))
    except RestoreError as exc:
        print(f"error: {exc}")
        return 2
    t = res.totals()
    print(
        f"Restored to MyST: {t['keys']} keys -> substitutions ({t['substitutions_defined']} defined), "
        f"{t['conrefs_as_includes']} conrefs -> includes ({t['include_files']} files), "
        f"{t['conrefs_as_substitutions']} inline conrefs -> substitutions, "
        f"{t['conditions']} conditions -> only blocks, {t['ids']} IDs -> anchors "
        f"({t['ids_dropped']} shared IDs dropped, as DITA-OT HTML5 does)"
    )
    print(f"Builds: {', '.join(b['name'] for b in res.builds)}")
    for u in res.unhandled:
        print(f"  unhandled: {u['owner']}: {u['reason']}")
    kinds: dict[str, int] = {}
    for f in res.findings:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    for kind, n in sorted(kinds.items()):
        print(f"  finding in the source: {n} x {kind} (details in _manifest/restore.json)")
    print(f"Wrote {args.out} (Sphinx project)")
    return 0


def _check(args: argparse.Namespace) -> int:
    try:
        res = check_topics(Path(args.map), Path(args.myst), Path(args.out), args.dita)
    except CheckError as exc:
        print(f"error: {exc}")
        return 2
    t = res.totals()
    print(
        f"{res.map}: {t['topics_passed']}/{t['topics']} topics pass in all {t['builds']} builds "
        f"({t['comparisons']} comparisons, {t['differences']} differences, "
        f"{t['page_differences']} page differences)"
    )
    for build, diffs in res.page_differences.items():
        for d in diffs:
            print(f"  PAGES [{build}] {d.detail}")
    for topic in res.topics:
        for b in topic.builds:
            for d in b.differences:
                print(
                    f"  FAIL {topic.topic} [{b.build}] {d.check}: {d.detail}  DITA={d.dita!r} MyST={d.myst!r}"
                )
    print(f"Wrote {args.out}/check.json")
    return 0 if res.ok else 1


def _links(args: argparse.Namespace) -> int:
    try:
        res = check_links(Path(args.check), Path(args.out), args.lychee)
    except LinkError as exc:
        print(f"error: {exc}")
        return 2
    t = res.totals()
    print(
        f"Links (lychee {res.lychee}, offline): {t['checked_links']} checked in {len(res.builds)} builds, "
        f"{t['external_links_listed']} external listed; {t['old_urls']} old URLs and {t['old_anchors']} "
        f"old anchors checked; {t['redirects']} redirects; problems: {t['broken_problems']} broken, "
        f"{t['navigation_problems']} navigation, {t['old-url_problems']} old URL"
    )
    for p in res.problems:
        print(f"  FAIL [{p.build}] {p.check} {p.page}: {p.detail}")
    print(f"Wrote {args.out}/links.json, {args.out}/redirects.json")
    return 0 if res.ok else 1


def _migrate(args: argparse.Namespace) -> int:
    try:
        rules = RuleSet(load_rules(Path(args.rules))) if args.rules else None
        r = migrate(Path(args.map), Path(args.out), args.dita, args.lychee, rules)
    except (ConvertError, RestoreError, CheckError, LinkError, RuleError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 2
    s = r["summary"]
    print(
        f"{r['map']}: gate {r['gate'].upper()} — {s['passed']} passed, {s['review']} need a writer, "
        f"{s['failed']} failed ({s['topics']} topics, {len(s['builds'])} builds)"
    )
    for m in r["metrics"]:
        print(f"  {m['metric']:32} target {m['target']:24} measured {m['measured']}")
    for i in r["review_items"]:
        print(f"  review: {i['topic']} <{i['element']}> line {i['line']}: {i['reason']}")
    print(f"Wrote {args.out}/myst (the migrated docs) and {args.out}/report/report.html")
    return 0 if r["gate"] == "pass" else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="manifest", description=__doc__)
    p.add_argument("--version", action="version", version=f"manifest {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    mig = sub.add_parser("migrate", help="run all five tools and write the migration report")
    mig.add_argument("map", help="path to the root .ditamap")
    mig.add_argument("--out", default="build", help="output folder")
    mig.add_argument("--dita", default=None, help="path to the DITA-OT launcher")
    mig.add_argument("--lychee", default=None, help="path to lychee")
    mig.add_argument("--rules", default=None, help="folder of mapping rules; only approved rules run")
    mig.set_defaults(func=_migrate)
    inv = sub.add_parser("inventory", help="count what is in the DITA source (tool 1)")
    inv.add_argument("map", help="path to the root .ditamap")
    inv.add_argument("--out", default="build/inventory.json", help="where to write the inventory JSON")
    inv.set_defaults(func=_inventory)
    conv = sub.add_parser("convert", help="DITA-OT build with placeholders, and prove they survived (tool 2)")
    conv.add_argument("map", help="path to the root .ditamap")
    conv.add_argument("--out", default="build/convert", help="output folder")
    conv.add_argument(
        "--dita", default=None, help="path to the DITA-OT launcher (default: $DITA_HOME or PATH)"
    )
    conv.set_defaults(func=_convert)
    rest = sub.add_parser(
        "restore", help="turn placeholders into MyST includes, substitutions, only blocks (tool 3)"
    )
    rest.add_argument("map", help="path to the root .ditamap")
    rest.add_argument("--convert", default="build/convert", help="convert output folder")
    rest.add_argument("--out", default="build/myst", help="Sphinx project to write")
    rest.set_defaults(func=_restore)
    chk = sub.add_parser("check", help="compare DITA-OT HTML5 with the Sphinx build, topic by topic (tool 4)")
    chk.add_argument("map", help="path to the root .ditamap")
    chk.add_argument("--myst", default="build/myst", help="the restored Sphinx project")
    chk.add_argument("--out", default="build/check", help="where to write both builds and check.json")
    chk.add_argument("--dita", default=None, help="path to the DITA-OT launcher")
    chk.set_defaults(func=_check)
    lnk = sub.add_parser(
        "links", help="broken links, navigation and old URLs, on the checked builds (tool 5)"
    )
    lnk.add_argument("--check", default="build/check", help="output folder of `manifest check`")
    lnk.add_argument("--out", default="build/links", help="where to write links.json and redirects.json")
    lnk.add_argument("--lychee", default=None, help="path to lychee (default: $LYCHEE or PATH)")
    lnk.set_defaults(func=_links)
    args = p.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
