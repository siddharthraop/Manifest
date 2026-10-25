"""Uploaded DITA: check a .zip before it is stored, unpack it safely, and package the results.

An upload is untrusted XML that DITA-OT will parse, so before anything runs:
  - size limits: 10 MB zipped, 60 MB unpacked, 2,000 files;
  - no path may leave the folder it is unpacked into (no "..", no absolute paths, no links);
  - only DITA, XML, images and plain text are unpacked; anything else is skipped;
  - every XML file's DOCTYPE, if it has one, must be a public OASIS DITA DTD with no internal subset,
    and no file may declare entities. That stops XML external entities (XXE) from reading files or
    URLs, the metadata server's included, into the published output.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path, PurePosixPath

MAX_ZIP = 10 * 1024 * 1024
MAX_UNPACKED = 60 * 1024 * 1024
MAX_FILES = 2000
ALLOWED = {".dita", ".ditamap", ".ditaval", ".xml", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".txt", ".md"}
XML_KINDS = {".dita", ".ditamap", ".ditaval", ".xml", ".svg"}
DOCTYPE = re.compile(
    rb"<!DOCTYPE\s+[\w:.-]+\s*(PUBLIC\s+\"([^\"]*)\"\s+\"[^\"]*\"|SYSTEM\s+\"[^\"]*\")?\s*(\[)?", re.S
)
SAFE_PUBLIC = re.compile(r"^-//OASIS//DTD (DITA|LIGHTWEIGHT DITA|XDITA)\b")
RESULT_README = """MANIFEST results

  myst/       the migrated Sphinx project (MyST Markdown). Build it with: sphinx-build myst out
  report/     report.html and report.json: every topic, every check, every build
  redirects.json  old DITA-OT URLs and where each one now lives

The checks compared DITA-OT HTML5 of your source with Sphinx of this MyST, topic by topic.
"""


class UploadError(ValueError):
    pass


def check_zip(data: bytes) -> list[str]:
    """Validate an upload without unpacking it. Returns the root map candidates."""
    if len(data) > MAX_ZIP:
        raise UploadError(
            f"the .zip is {len(data) // 1024 // 1024} MB; the limit is {MAX_ZIP // 1024 // 1024} MB"
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UploadError("that isn't a .zip file") from None
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_FILES:
        raise UploadError(f"the .zip has {len(infos)} files; the limit is {MAX_FILES}")
    if sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise UploadError(f"the files add up to more than {MAX_UNPACKED // 1024 // 1024} MB unpacked")
    maps = []
    for i in infos:
        p = PurePosixPath(i.filename)
        if (
            p.is_absolute()
            or ".." in p.parts
            or i.filename.startswith(("/", "\\"))
            or (i.external_attr >> 28) == 0xA
        ):
            raise UploadError(f"{i.filename}: paths must stay inside the .zip")
        if p.name.startswith(".") or "__MACOSX" in p.parts:
            continue
        if p.suffix.lower() not in ALLOWED:
            continue  # stray files (scripts, build output) are skipped, never unpacked
        if p.suffix.lower() == ".ditamap":
            maps.append(str(p))
    if not maps:
        raise UploadError("the .zip has no .ditamap")
    return sorted(maps, key=lambda m: (m.count("/"), m))


def check_xml(name: str, head: bytes) -> None:
    if b"<!ENTITY" in head:
        raise UploadError(f"{name}: entity declarations aren't accepted")
    m = DOCTYPE.search(head)
    if not m:
        return
    public = (m.group(2) or b"").decode("utf-8", "replace")
    if m.group(1) and not public:
        raise UploadError(f"{name}: a SYSTEM-only DOCTYPE isn't accepted; use the OASIS public identifier")
    if public and not SAFE_PUBLIC.match(public):
        raise UploadError(f"{name}: DOCTYPE {public!r} isn't a DITA DTD")
    if m.group(3):
        raise UploadError(f"{name}: a DOCTYPE with an internal subset isn't accepted")


def extract(data: bytes, dest: Path) -> list[str]:
    """Unpack a checked upload into dest. Returns the root map candidates, shallowest first."""
    maps = check_zip(data)
    zf = zipfile.ZipFile(io.BytesIO(data))
    root = dest.resolve()
    for i in zf.infolist():
        p = PurePosixPath(i.filename)
        if i.is_dir() or p.name.startswith(".") or "__MACOSX" in p.parts or p.suffix.lower() not in ALLOWED:
            continue
        target = (dest / Path(*p.parts)).resolve()
        if not target.is_relative_to(root):
            raise UploadError(f"{i.filename}: paths must stay inside the .zip")
        body = zf.read(i)
        if p.suffix.lower() in XML_KINDS:
            check_xml(i.filename, body)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return maps


def find_map(dest: Path, wanted: str, candidates: list[str]) -> Path:
    """The root map the user named, or the only (or shallowest) .ditamap."""
    choice = wanted.strip().lstrip("/") or candidates[0]
    if choice not in candidates:
        raise UploadError(f"{choice} isn't in the .zip; it has: {', '.join(candidates[:10])}")
    return dest / Path(*PurePosixPath(choice).parts)


def summary_text(report: dict[str, object]) -> str:
    """The promise, in plain words: every difference is named. Nothing that differs goes unreported."""
    import json as _json

    r = _json.loads(_json.dumps(report))
    s = r["summary"]
    lines = [
        RESULT_README,
        f"Gate: {r['gate'].upper()}",
        f"{s['topics']} topics in {len(s['builds'])} build(s): {s['passed']} passed, "
        f"{s.get('fixed', 0)} fixed, {s['review']} need a writer, {s['failed']} failed.",
        "",
    ]
    lines += [f"  {m['metric']}: {m['measured']} (target {m['target']})" for m in r["metrics"]]
    bad = [t for t in r["topics"] if t["status"] != "pass"]
    if bad:
        lines += ["", "Topics that aren't clean (the MyST page exists, but check it against the report):"]
        for t in bad:
            checks = sorted({d["check"] for d in t["differences"]}) or ["review item"]
            lines.append(f"  {t['status'].upper():7} {t['topic']}: {', '.join(checks)}. {t['note']}")
    else:
        lines += ["", "Every topic passed every check in every build."]
    return "\n".join(lines) + "\n"


def result_zip(out: Path, target: Path) -> Path:
    """Package what the user takes away: the MyST project, the report and the redirects."""
    import json as _json

    target.parent.mkdir(parents=True, exist_ok=True)
    report = _json.loads((out / "report" / "report.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", summary_text(report))
        for sub in ("myst", "report"):
            for f in sorted((out / sub).rglob("*")):
                if f.is_file() and "_build" not in f.parts:
                    zf.write(f, str(f.relative_to(out)))
        redirects = out / "links" / "redirects.json"
        if redirects.exists():
            zf.write(redirects, "redirects.json")
    return target
