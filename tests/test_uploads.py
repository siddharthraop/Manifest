"""Migrate your own DITA (ADR 0010): upload a .zip, get MyST and a report that names every difference.
Uploads are untrusted XML, so the checks before anything runs are tested as hard as the happy path."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from conftest import ROOT, needs_toolchain

from manifest.uploads import UploadError, check_xml, check_zip, extract, find_map

DITA = b'<?xml version="1.0"?>\n<!DOCTYPE topic PUBLIC "-//OASIS//DTD DITA Topic//EN" "topic.dtd">\n<topic id="t"><title>T</title></topic>'
MAP = b'<?xml version="1.0"?>\n<!DOCTYPE map PUBLIC "-//OASIS//DTD DITA Map//EN" "map.dtd">\n<map><topicref href="t.dita"/></map>'


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def zip_dir(folder: Path, prefix: str = "") -> bytes:
    return make_zip(
        {prefix + str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    )


# ------------------------------------------------------------------ what is refused, before anything runs


@pytest.mark.parametrize(("files", "reason"), [
    ({"docs/t.dita": DITA}, "no .ditamap"),
    ({"../evil.ditamap": MAP}, "inside the .zip"),
    ({"/abs.ditamap": MAP}, "inside the .zip"),
])  # fmt: skip
def test_bad_uploads_are_refused(files: dict[str, bytes], reason: str) -> None:
    with pytest.raises(UploadError, match=reason):
        check_zip(make_zip(files))


def test_not_a_zip_and_too_big_are_refused() -> None:
    with pytest.raises(UploadError, match="isn't a .zip"):
        check_zip(b"hello")
    with pytest.raises(UploadError, match="limit"):
        check_zip(b"0" * (11 * 1024 * 1024))


@pytest.mark.parametrize("head", [
    b'<?xml version="1.0"?><!DOCTYPE topic [<!ENTITY x SYSTEM "file:///etc/passwd">]><topic>&x;</topic>',
    b'<!DOCTYPE topic SYSTEM "http://metadata.google.internal/computeMetadata/v1/">',
    b'<!DOCTYPE topic PUBLIC "-//EVIL//DTD X//EN" "http://evil.example/x.dtd">',
    b'<!DOCTYPE topic PUBLIC "-//OASIS//DTD DITA Topic//EN" "topic.dtd" [ <!ATTLIST topic x CDATA "y"> ]>',
])  # fmt: skip
def test_xml_that_could_read_files_or_urls_is_refused(head: bytes) -> None:
    with pytest.raises(UploadError):
        check_xml("t.dita", head)


def test_dita_doctypes_are_accepted() -> None:
    check_xml("t.dita", DITA)
    check_xml("m.ditamap", MAP)
    check_xml("plain.xml", b"<root/>")


def test_extract_unpacks_and_finds_the_root_map(tmp_path: Path) -> None:
    data = make_zip(
        {"docs/product.ditamap": MAP, "docs/t.dita": DITA, "docs/keys/k.ditamap": MAP, "__MACOSX/._x": b""}
    )
    maps = extract(data, tmp_path)
    assert maps == ["docs/product.ditamap", "docs/keys/k.ditamap"]
    assert find_map(tmp_path, "", maps) == tmp_path / "docs" / "product.ditamap"
    assert (tmp_path / "docs" / "t.dita").read_bytes() == DITA and not (tmp_path / "__MACOSX").exists()
    assert not (tmp_path / "docs" / "run.sh").exists(), (
        "files that aren't DITA, XML, images or text are skipped"
    )
    with pytest.raises(UploadError, match="isn't in the .zip"):
        find_map(tmp_path, "other.ditamap", maps)


# ------------------------------------------------------------------ the API takes uploads from anyone


def test_anyone_can_upload_and_the_job_gets_the_zip(tmp_path: Path) -> None:
    from test_api import FakeLauncher, FakeVerifier, TestClient

    from api.main import create_app
    from manifest.store import LocalBlobs, MemoryStore

    store, launcher = MemoryStore(), FakeLauncher()
    client = TestClient(
        create_app(
            store, LocalBlobs(tmp_path), FakeVerifier(), launcher, maps=lambda: [], public_runs_per_day=2
        )
    )
    data = make_zip({"docs/product.ditamap": MAP, "docs/t.dita": DITA})
    r = client.post(
        "/api/uploads", params={"name": "mine.zip"}, content=data, headers={"Content-Type": "application/zip"}
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    (env,) = launcher.calls
    assert (
        env["MANIFEST_UPLOAD"] == f"uploads/{run_id}.zip"
        and env["MANIFEST_UPLOAD_MAP"] == "docs/product.ditamap"
    )
    assert (tmp_path / "uploads" / f"{run_id}.zip").read_bytes() == data
    assert store.get("runs", run_id)["map"] == "mine.zip: docs/product.ditamap"
    busy = client.post("/api/uploads", content=data)
    assert busy.status_code == 409, "one run at a time"
    store.merge("runs", run_id, {"status": "succeeded"}, "manifest-job", "run.finish")
    assert client.post("/api/uploads", content=data).status_code == 202
    store.merge(
        "runs", launcher.calls[-1]["MANIFEST_RUN_ID"], {"status": "succeeded"}, "manifest-job", "run.finish"
    )
    capped = client.post("/api/uploads", content=data)
    assert capped.status_code == 429 and "public runs" in capped.json()["detail"]
    assert client.post("/api/uploads", content=b"nope").status_code == 422
    assert client.post("/api/uploads", params={"map": "x.ditamap"}, content=data).status_code in (422, 429)


# ------------------------------------------------------------------ end to end: the user's DITA in, docs as code out


@needs_toolchain
def test_an_uploaded_dita_set_comes_back_as_myst_with_its_report(tmp_path: Path) -> None:
    from manifest.cloud import run_job
    from manifest.store import LocalBlobs, MemoryStore

    store, blobs = MemoryStore(), LocalBlobs(tmp_path / "bucket")
    blobs.write("uploads/u1.zip", zip_dir(ROOT / "help", "help/"), "application/zip")
    env = {"MANIFEST_RUN_ID": "u1", "MANIFEST_MODE": "migrate", "MANIFEST_MAP": "help.zip: help/manifest-help.ditamap",
           "MANIFEST_UPLOAD": "uploads/u1.zip", "MANIFEST_UPLOAD_MAP": "help/manifest-help.ditamap"}  # fmt: skip
    result = run_job(store, blobs, env, tmp_path / "work", ROOT / "corpus", repo_rules=ROOT / "rules")
    assert result["status"] == "succeeded" and result["gate"] == "pass"
    run = store.get("runs", "u1")
    assert run["stage"] == "done" and run["source_prefix"] == "source/help"
    assert (tmp_path / "bucket" / "runs" / "u1" / "source" / "help" / "topics" / "c-fidelity.dita").exists()
    with zipfile.ZipFile(tmp_path / "bucket" / "runs" / "u1" / "result.zip") as zf:
        names = set(zf.namelist())
        readme = zf.read("README.txt").decode()
        report = json.loads(zf.read("report/report.json"))
    assert {"myst/conf.py", "myst/topics/c-fidelity.md", "report/report.html", "redirects.json"} <= names
    assert "Gate: PASS" in readme and "Unreported losses: 0" in readme
    assert "REVIEW" in readme and "gs-first-migration.dita" in readme, "what isn't clean is named, not hidden"
    assert report["summary"]["topics"] == 29
