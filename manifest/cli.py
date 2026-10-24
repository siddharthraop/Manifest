"""Command line: `manifest inventory tpu/tpu.ditamap`. More commands arrive with each tool."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from manifest import __version__
from manifest.tools.convert import ConvertError, convert_map
from manifest.tools.inventory import inventory_map


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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="manifest", description=__doc__)
    p.add_argument("--version", action="version", version=f"manifest {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
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
    args = p.parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
