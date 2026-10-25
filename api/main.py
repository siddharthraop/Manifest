"""The MANIFEST API (Cloud Run service, min 0 / max 1 instance, request-based billing).

Anyone can read: maps, runs, reports, rules and reviews are public sample content. Anything that
writes or spends quota (starting a run, approving or rejecting a rule, reading the audit trail)
needs a Google ID token from an allowlisted, verified address (ADR 0008).

Run it locally against memory:  uvicorn --factory api.main:create_local_app
On Cloud Run:                   uvicorn --factory api.main:create_app_from_env
"""

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from manifest import __version__
from manifest.agent import DEFAULT_MODEL
from manifest.cloud import MODES, list_maps, new_run_id
from manifest.store import Blobs, LocalBlobs, MemoryStore, NotFound, Store, now
from manifest.uploads import MAX_ZIP, UploadError, check_zip

ACTIVE_STATUSES = ("queued", "running")
SAFE_PATH = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._/-]{1,300}$")
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; font-src 'self'; img-src 'self' data:"


# ---------------------------------------------------------------- sign-in


class Verifier(Protocol):
    configured: bool

    def verify(self, token: str) -> str:
        """Return the verified, allowlisted email address, or raise PermissionError."""
        ...


class GoogleVerifier:
    """Sign In With Google ID tokens, verified as Google documents (signature, aud, exp, iss)."""

    ISSUERS = ("accounts.google.com", "https://accounts.google.com")

    def __init__(self, client_id: str, allowed: set[str]) -> None:
        self.client_id, self.allowed = client_id, {a.strip().lower() for a in allowed if a.strip()}
        self.configured = bool(client_id and self.allowed)

    def verify(self, token: str) -> str:
        from google.auth.transport import requests
        from google.oauth2 import id_token

        try:
            info = id_token.verify_oauth2_token(token, requests.Request(), self.client_id)  # type: ignore[no-untyped-call]
        except ValueError as exc:
            raise PermissionError(f"the sign-in token isn't valid: {exc}") from exc
        if info.get("iss") not in self.ISSUERS:
            raise PermissionError("the sign-in token wasn't issued by Google")
        email = str(info.get("email", "")).lower()
        if not info.get("email_verified") or email not in self.allowed:
            raise PermissionError(f"{email or 'this account'} may read but not run or approve")
        return email


# ---------------------------------------------------------------- starting the job


class Launcher(Protocol):
    def run(self, env: dict[str, str]) -> str:
        """Start one job execution with these environment overrides; return its name."""
        ...


class CloudRunLauncher:
    def __init__(self, job: str) -> None:
        self.job = job  # projects/P/locations/R/jobs/manifest-migrate

    def run(self, env: dict[str, str]) -> str:
        from google.cloud import run_v2

        o = run_v2.RunJobRequest.Overrides
        req = run_v2.RunJobRequest(
            name=self.job,
            overrides=o(
                container_overrides=[
                    o.ContainerOverride(env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()])
                ]
            ),
        )
        op = run_v2.JobsClient().run_job(request=req)
        return str(getattr(op.metadata, "name", "") or op.operation.name)


# ---------------------------------------------------------------- request bodies


class StartRun(BaseModel):
    map: str = "corpus/tpu/tpu.ditamap"
    mode: str = Field("migrate", description="migrate, replay or live")


class Decision(BaseModel):
    reason: str = Field("", max_length=2000)


# ---------------------------------------------------------------- the app


def create_app(
    store: Store,
    blobs: Blobs,
    verifier: Verifier,
    launcher: Launcher,
    maps: Callable[[], list[str]] = list_maps,
    origins: list[str] | None = None,
    public_runs_per_day: int = 10,
) -> FastAPI:
    app = FastAPI(
        title="MANIFEST API",
        version=__version__,
        description="Runs, reports, mapping rules and reviews for MANIFEST migrations.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or [],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def signed_in(authorization: Annotated[str, Header()] = "") -> str:
        if not verifier.configured:
            raise HTTPException(503, "Sign-in isn't configured on this deployment, so it is read-only.")
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                401, "Sign in with Google to do this.", headers={"WWW-Authenticate": "Bearer"}
            )
        try:
            return verifier.verify(authorization.removeprefix("Bearer ").strip())
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    User = Annotated[str, Depends(signed_in)]

    def get_or_404(collection: str, doc_id: str) -> dict[str, Any]:
        try:
            return store.get(collection, doc_id)
        except NotFound:
            raise HTTPException(404, f"No {collection[:-1]} {doc_id}.") from None

    @app.get("/api/health", tags=["service"])
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/config", tags=["service"])
    def config() -> dict[str, Any]:
        """What the console needs to know: sign-in, modes, the default model."""
        client_id = getattr(verifier, "client_id", "")
        return {
            "sign_in": verifier.configured,
            "client_id": client_id if verifier.configured else "",
            "modes": list(MODES),
            "model": DEFAULT_MODEL,
            "version": __version__,
            "uploads": {"max_mb": MAX_ZIP // 1024 // 1024, "runs_per_day": public_runs_per_day},
        }

    @app.get("/api/maps", tags=["runs"])
    def list_maps_with_runs() -> list[dict[str, Any]]:
        """Every DITA map in the corpus, with its latest run."""
        out = []
        for m in maps():
            runs = store.list("runs", limit=1, map=m)
            out.append({"map": m, "last_run": runs[0] if runs else None})
        return out

    @app.get("/api/runs", tags=["runs"])
    def list_runs(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
        return store.list("runs", limit=limit)

    @app.get("/api/runs/{run_id}", tags=["runs"])
    def get_run(run_id: str) -> dict[str, Any]:
        return get_or_404("runs", run_id)

    @app.get("/api/runs/{run_id}/files/{path:path}", tags=["runs"])
    def get_run_file(run_id: str, path: str) -> Response:
        """A stored file of the run: report/report.html, report/report.json, agent/run.json, myst/…"""
        if not SAFE_PATH.match(run_id) or not SAFE_PATH.match(path):
            raise HTTPException(400, "That isn't a file path.")
        try:
            data, ctype = blobs.read(f"runs/{run_id}/{path}")
        except NotFound:
            raise HTTPException(404, f"Run {run_id} has no file {path}.") from None
        if path.endswith((".md", ".jsonl", ".xml", ".dita")):
            ctype = "text/plain; charset=utf-8"
        headers = {"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}
        if ctype.startswith("text/html"):
            headers["Content-Security-Policy"] = REPORT_CSP
        return Response(data, media_type=ctype, headers=headers)

    @app.post("/api/uploads", status_code=202, tags=["runs"])
    async def upload(request: Request, map: str = "", name: str = "upload.zip") -> dict[str, Any]:
        """Migrate your own DITA: send a .zip as the request body (Content-Type: application/zip).
        `map` names the root map inside the .zip; without it, the shallowest .ditamap is used.
        Anyone may upload; uploads share one queue and a daily limit with every other public run."""
        data = await request.body()
        if len(data) > MAX_ZIP:
            raise HTTPException(413, f"The .zip is larger than {MAX_ZIP // 1024 // 1024} MB.")
        try:
            candidates = check_zip(data)
        except UploadError as exc:
            raise HTTPException(422, str(exc)) from exc
        root = map.strip().lstrip("/") or candidates[0]
        if root not in candidates:
            raise HTTPException(422, f"{root} isn't in the .zip. It has: {', '.join(candidates[:10])}")
        guard_queue()
        run_id = new_run_id()
        label = f"{Path(name).name or 'upload.zip'}: {root}"
        blobs.write(f"uploads/{run_id}.zip", data, "application/zip")
        store.put(
            "runs",
            run_id,
            {
                "map": label,
                "mode": "migrate",
                "status": "queued",
                "queued_at": now(),
                "triggered_by": "public",
                "upload": True,
            },
            "public",
            "run.queue",
        )
        return start(
            run_id,
            {
                "MANIFEST_RUN_ID": run_id,
                "MANIFEST_MAP": label,
                "MANIFEST_MODE": "migrate",
                "MANIFEST_TRIGGERED_BY": "public",
                "MANIFEST_UPLOAD": f"uploads/{run_id}.zip",
                "MANIFEST_UPLOAD_MAP": root,
            },
            "public",
        )

    def guard_queue() -> None:
        active = [r for s in ACTIVE_STATUSES for r in store.list("runs", limit=5, status=s)]
        if active:
            raise HTTPException(
                409,
                f"Run {active[0]['id']} is still {active[0]['status']}. One run at a time "
                "keeps this demo inside the free tier; try again in a few minutes.",
            )
        today = now()[:10]
        used = [
            r
            for r in store.list("runs", limit=500, triggered_by="public")
            if str(r.get("queued_at", "")).startswith(today)
        ]
        if len(used) >= public_runs_per_day:
            raise HTTPException(
                429, f"Today's {public_runs_per_day} public runs are used up. Try again tomorrow (UTC)."
            )

    def start(run_id: str, env: dict[str, str], actor: str) -> dict[str, Any]:
        try:
            execution = launcher.run(env)
        except Exception as exc:
            store.merge(
                "runs",
                run_id,
                {"status": "failed", "error": f"couldn't start the job: {exc}"[:1000]},
                actor,
                "run.fail",
            )
            raise HTTPException(502, "The migration job couldn't be started.") from exc
        store.merge("runs", run_id, {"execution": execution}, actor, "run.execute")
        return {"id": run_id, "status": "queued", "execution": execution}

    @app.post("/api/runs", status_code=202, tags=["runs"])
    def start_run(body: StartRun, user: User) -> dict[str, Any]:
        """Start a migration. One run at a time protects the free-tier quota."""
        if body.mode not in MODES:
            raise HTTPException(422, f"mode must be one of {', '.join(MODES)}")
        if body.map not in maps():
            raise HTTPException(422, f"{body.map} isn't a map in the corpus")
        active = [r for s in ACTIVE_STATUSES for r in store.list("runs", limit=5, status=s)]
        if active:
            raise HTTPException(
                409, f"Run {active[0]['id']} is still {active[0]['status']}; wait for it to finish."
            )
        run_id = new_run_id()
        store.put(
            "runs",
            run_id,
            {
                "map": body.map,
                "mode": body.mode,
                "status": "queued",
                "queued_at": now(),
                "triggered_by": user,
            },
            user,
            "run.queue",
        )
        env = {
            "MANIFEST_RUN_ID": run_id,
            "MANIFEST_MAP": body.map,
            "MANIFEST_MODE": body.mode,
            "MANIFEST_TRIGGERED_BY": user,
        }
        try:
            execution = launcher.run(env)
        except Exception as exc:
            store.merge(
                "runs",
                run_id,
                {"status": "failed", "error": f"couldn't start the job: {exc}"[:1000]},
                user,
                "run.fail",
            )
            raise HTTPException(502, "The migration job couldn't be started.") from exc
        store.merge("runs", run_id, {"execution": execution}, user, "run.execute")
        return {"id": run_id, "status": "queued", "execution": execution}

    @app.get("/api/rules", tags=["rules"])
    def list_rules(status: str | None = None) -> list[dict[str, Any]]:
        return store.list("rules", limit=200, **({"status": status} if status else {}))

    @app.get("/api/rules/{rule_id}", tags=["rules"])
    def get_rule(rule_id: str) -> dict[str, Any]:
        return get_or_404("rules", rule_id)

    def decide(rule_id: str, user: str, status: str, reason: str) -> dict[str, Any]:
        rule = get_or_404("rules", rule_id)
        if rule.get("status") != "proposed":
            raise HTTPException(409, f"Rule {rule_id} is already {rule.get('status')}.")
        verb = "approved" if status == "approved" else "rejected"
        prov = {**rule.get("provenance", {}), f"{verb}_by": user, f"{verb}_at": now()}
        if reason:
            prov[f"{verb}_because"] = reason
        store.merge("rules", rule_id, {"status": status, "provenance": prov}, user, f"rule.{status[:-1]}")
        return get_or_404("rules", rule_id)

    @app.post("/api/rules/{rule_id}/approve", tags=["rules"])
    def approve_rule(rule_id: str, user: User, body: Decision | None = None) -> dict[str, Any]:
        """Approve a proposed rule. Every later run applies it without calling Gemini."""
        return decide(rule_id, user, "approved", body.reason if body else "")

    @app.post("/api/rules/{rule_id}/reject", tags=["rules"])
    def reject_rule(rule_id: str, body: Decision, user: User) -> dict[str, Any]:
        """Reject a proposed rule, saying why. It is kept, with who rejected it, for the record."""
        if not body.reason.strip():
            raise HTTPException(422, "Say why the rule is rejected.")
        return decide(rule_id, user, "rejected", body.reason.strip())

    @app.get("/api/reviews", tags=["reviews"])
    def list_reviews(status: str | None = "open") -> list[dict[str, Any]]:
        return store.list("reviews", limit=200, **({"status": status} if status else {}))

    @app.get("/api/audit", tags=["service"])
    def audit(user: User, limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
        """Every write: who, what, when."""
        return store.list("audit", limit=limit)

    return app


def create_app_from_env() -> FastAPI:  # pragma: no cover - wiring, exercised by deploy.yml's smoke test
    from manifest.store import FirestoreStore, GcsBlobs

    env = os.environ
    return create_app(
        FirestoreStore(),
        GcsBlobs(env["MANIFEST_BUCKET"]),
        GoogleVerifier(
            env.get("MANIFEST_OAUTH_CLIENT_ID", ""), set(env.get("MANIFEST_ALLOWED_EMAILS", "").split(","))
        ),
        CloudRunLauncher(env["MANIFEST_JOB"]),
        origins=[
            o for o in env.get("MANIFEST_CORS_ORIGINS", "https://siddharthraop.github.io").split(",") if o
        ],
    )


def create_local_app() -> FastAPI:  # pragma: no cover - for a laptop: memory store, nothing runs
    class NoLauncher:
        def run(self, env: dict[str, str]) -> str:
            raise RuntimeError("no job runner locally; use `manifest migrate`")

    return create_app(MemoryStore(), LocalBlobs(Path("build/blobs")), GoogleVerifier("", set()), NoLauncher())
