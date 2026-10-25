"""The API (build step 4, ADR 0008): public reads, signed-in writes, one run at a time, audited.

Firestore, Cloud Storage, Cloud Run and Google sign-in are replaced by in-memory fakes with the
same interfaces; the job and the store are tested against the real pipeline in test_cloud_job.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.main import GoogleVerifier, create_app  # noqa: E402
from manifest.store import LocalBlobs, MemoryStore  # noqa: E402

MAPS = ["corpus/tpu/tpu.ditamap", "corpus/eos/eos.ditamap"]
WRITER = {"Authorization": "Bearer writer-token"}
STRANGER = {"Authorization": "Bearer stranger-token"}


class FakeVerifier:
    configured = True
    client_id = "test-client.apps.googleusercontent.com"

    def verify(self, token: str) -> str:
        if token == "writer-token":
            return "writer@example.com"
        raise PermissionError("stranger@example.com may read but not run or approve")


class FakeLauncher:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail

    def run(self, env: dict[str, str]) -> str:
        if self.fail:
            raise RuntimeError("PERMISSION_DENIED")
        self.calls.append(env)
        return f"projects/p/locations/us-central1/jobs/manifest-migrate/executions/e-{len(self.calls)}"


def make(
    tmp_path: Path, launcher: FakeLauncher | None = None, verifier: Any = None
) -> tuple[TestClient, MemoryStore, FakeLauncher]:
    store, launcher = MemoryStore(), launcher or FakeLauncher()
    app = create_app(store, LocalBlobs(tmp_path), verifier or FakeVerifier(), launcher, maps=lambda: MAPS,
                     origins=["https://siddharthraop.github.io"])  # fmt: skip
    return TestClient(app), store, launcher


PROPOSED = {"id": "slice-planning-tables", "match": "choicetable hazardstatement", "status": "proposed",
            "description": "Both become simple tables.", "xslt": '<xsl:template match="choicetable"/>',
            "provenance": {"run": "r1", "topic": "topics/tpu-v5e-slice-planning.dita", "proposed_by": "gemini-3.8-flash"}}  # fmt: skip


# ------------------------------------------------------------------ public reads


def test_anyone_can_read_maps_runs_rules_and_reports(tmp_path: Path) -> None:
    client, store, _ = make(tmp_path)
    store.put(
        "runs", "r1", {"map": MAPS[0], "status": "succeeded", "gate": "pass"}, "manifest-job", "run.finish"
    )
    store.put("rules", PROPOSED["id"], PROPOSED, "manifest-job", "rule.propose")
    (tmp_path / "runs" / "r1" / "report").mkdir(parents=True)
    (tmp_path / "runs" / "r1" / "report" / "report.html").write_text("<h1>Migration report</h1>")

    assert client.get("/api/health").json()["ok"] is True
    maps = client.get("/api/maps").json()
    assert (
        [m["map"] for m in maps] == MAPS and maps[0]["last_run"]["id"] == "r1" and maps[1]["last_run"] is None
    )
    assert client.get("/api/runs").json()[0]["gate"] == "pass"
    assert client.get("/api/runs/r1").json()["status"] == "succeeded"
    assert client.get("/api/rules", params={"status": "proposed"}).json()[0]["id"] == PROPOSED["id"]
    report = client.get("/api/runs/r1/files/report/report.html")
    assert report.status_code == 200 and "Migration report" in report.text
    assert "default-src 'none'" in report.headers["content-security-policy"], "stored HTML runs no scripts"


def test_missing_things_are_404_and_paths_cant_escape_the_run(tmp_path: Path) -> None:
    client, _, _ = make(tmp_path)
    (tmp_path / "secret.txt").write_text("not a run file")
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/rules/nope").status_code == 404
    assert client.get("/api/runs/r1/files/report/report.html").status_code == 404
    assert client.get("/api/runs/r1/files/..%2F..%2Fsecret.txt").status_code in (400, 404)
    assert client.get("/api/runs/r1/files/a/../../secret.txt").status_code in (400, 404)


def test_config_tells_the_console_whether_it_can_sign_in(tmp_path: Path) -> None:
    client, _, _ = make(tmp_path)
    cfg = client.get("/api/config").json()
    assert cfg["sign_in"] is True and cfg["client_id"].endswith("googleusercontent.com")
    assert cfg["modes"] == ["migrate", "replay", "live"] and cfg["model"] == "gemini-3.8-flash"
    off, _, _ = make(tmp_path, verifier=GoogleVerifier("", set()))
    assert off.get("/api/config").json() == {**cfg, "sign_in": False, "client_id": ""}


# ------------------------------------------------------------------ writes need a writer


@pytest.mark.parametrize(("method", "path", "body"), [
    ("post", "/api/runs", {"map": MAPS[0], "mode": "migrate"}),
    ("post", f"/api/rules/{PROPOSED['id']}/approve", None),
    ("post", f"/api/rules/{PROPOSED['id']}/reject", {"reason": "no"}),
    ("get", "/api/audit", None),
])  # fmt: skip
def test_writes_need_a_signed_in_writer(tmp_path: Path, method: str, path: str, body: Any) -> None:
    client, store, launcher = make(tmp_path)
    store.put("rules", PROPOSED["id"], PROPOSED, "manifest-job", "rule.propose")
    call = getattr(client, method)
    kwargs = {"json": body} if body is not None else {}
    assert call(path, **kwargs).status_code == 401
    assert call(path, headers=STRANGER, **kwargs).status_code == 403
    assert launcher.calls == [] and store.get("rules", PROPOSED["id"])["status"] == "proposed"


def test_without_sign_in_configured_the_deployment_is_read_only(tmp_path: Path) -> None:
    client, _, launcher = make(tmp_path, verifier=GoogleVerifier("", {"writer@example.com"}))
    r = client.post("/api/runs", json={"map": MAPS[0]}, headers=WRITER)
    assert r.status_code == 503 and "read-only" in r.json()["detail"] and launcher.calls == []


# ------------------------------------------------------------------ runs


def test_a_writer_starts_a_run_and_the_job_gets_its_settings(tmp_path: Path) -> None:
    client, store, launcher = make(tmp_path)
    r = client.post("/api/runs", json={"map": MAPS[0], "mode": "replay"}, headers=WRITER)
    assert r.status_code == 202
    run_id = r.json()["id"]
    (env,) = launcher.calls
    assert env == {"MANIFEST_RUN_ID": run_id, "MANIFEST_MAP": MAPS[0], "MANIFEST_MODE": "replay",
                   "MANIFEST_TRIGGERED_BY": "writer@example.com"}  # fmt: skip
    run = store.get("runs", run_id)
    assert run["status"] == "queued" and run["execution"].endswith("/executions/e-1")
    assert run["triggered_by"] == "writer@example.com"


def test_one_run_at_a_time(tmp_path: Path) -> None:
    client, store, launcher = make(tmp_path)
    assert client.post("/api/runs", json={"map": MAPS[0]}, headers=WRITER).status_code == 202
    second = client.post("/api/runs", json={"map": MAPS[1]}, headers=WRITER)
    assert (
        second.status_code == 409 and "still queued" in second.json()["detail"] and len(launcher.calls) == 1
    )


@pytest.mark.parametrize("body", [{"map": "corpus/../etc/passwd"}, {"map": MAPS[0], "mode": "scripted"}])
def test_only_corpus_maps_and_known_modes_run(tmp_path: Path, body: dict[str, str]) -> None:
    client, _, launcher = make(tmp_path)
    assert client.post("/api/runs", json=body, headers=WRITER).status_code == 422 and launcher.calls == []


def test_a_job_that_cant_start_marks_the_run_failed(tmp_path: Path) -> None:
    client, store, _ = make(tmp_path, launcher=FakeLauncher(fail=True))
    r = client.post("/api/runs", json={"map": MAPS[0]}, headers=WRITER)
    assert r.status_code == 502
    (run,) = store.list("runs")
    assert run["status"] == "failed" and "PERMISSION_DENIED" in run["error"]
    assert client.post("/api/runs", json={"map": MAPS[0]}, headers=WRITER).status_code == 502, (
        "a failed run does not block the next one (502 again, not 409)"
    )


# ------------------------------------------------------------------ rules: the human gate


def test_approving_a_rule_records_who_and_when(tmp_path: Path) -> None:
    client, store, _ = make(tmp_path)
    store.put("rules", PROPOSED["id"], PROPOSED, "manifest-job", "rule.propose")
    r = client.post(f"/api/rules/{PROPOSED['id']}/approve", headers=WRITER)
    assert r.status_code == 200
    rule = r.json()
    assert rule["status"] == "approved" and rule["provenance"]["approved_by"] == "writer@example.com"
    assert rule["provenance"]["run"] == "r1" and "approved_at" in rule["provenance"], "provenance is kept"
    again = client.post(f"/api/rules/{PROPOSED['id']}/approve", headers=WRITER)
    assert again.status_code == 409


def test_rejecting_a_rule_needs_a_reason_and_keeps_it(tmp_path: Path) -> None:
    client, store, _ = make(tmp_path)
    store.put("rules", PROPOSED["id"], PROPOSED, "manifest-job", "rule.propose")
    url = f"/api/rules/{PROPOSED['id']}/reject"
    assert client.post(url, json={"reason": "  "}, headers=WRITER).status_code == 422
    rule = client.post(
        url, json={"reason": "Hazard statements need a note, not a table."}, headers=WRITER
    ).json()
    assert rule["status"] == "rejected" and rule["provenance"]["rejected_by"] == "writer@example.com"
    assert rule["provenance"]["rejected_because"].startswith("Hazard statements")


def test_every_write_is_in_the_audit_trail(tmp_path: Path) -> None:
    client, store, _ = make(tmp_path)
    store.put("rules", PROPOSED["id"], PROPOSED, "manifest-job", "rule.propose")
    client.post("/api/runs", json={"map": MAPS[0]}, headers=WRITER)
    client.post(f"/api/rules/{PROPOSED['id']}/approve", headers=WRITER)
    trail = client.get("/api/audit", headers=WRITER).json()
    actions = sorted((e["actor"], e["action"]) for e in trail)
    assert actions == sorted([("manifest-job", "rule.propose"), ("writer@example.com", "run.queue"),
                              ("writer@example.com", "run.execute"), ("writer@example.com", "rule.approve")])  # fmt: skip


def test_the_console_origin_may_call_the_api(tmp_path: Path) -> None:
    client, _, _ = make(tmp_path)
    r = client.options("/api/runs", headers={"Origin": "https://siddharthraop.github.io",
                                             "Access-Control-Request-Method": "POST"})  # fmt: skip
    assert r.headers["access-control-allow-origin"] == "https://siddharthraop.github.io"
    other = client.options(
        "/api/runs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in other.headers


def test_the_google_verifier_refuses_bad_tokens_and_strangers(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.oauth2 import id_token

    v = GoogleVerifier("cid", {"Writer@Example.com"})
    claims = {"iss": "https://accounts.google.com", "email": "writer@example.com", "email_verified": True}
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda token, req, aud: dict(claims))
    assert v.verify("t") == "writer@example.com"
    for bad in ({"iss": "https://evil.example"}, {"email_verified": False}, {"email": "other@example.com"}):
        monkeypatch.setattr(id_token, "verify_oauth2_token", lambda token, req, aud, b=bad: {**claims, **b})
        with pytest.raises(PermissionError):
            v.verify("t")

    def invalid(token: str, req: Any, aud: str) -> dict[str, Any]:
        raise ValueError("Token expired")

    monkeypatch.setattr(id_token, "verify_oauth2_token", invalid)
    with pytest.raises(PermissionError, match="Token expired"):
        v.verify("t")
