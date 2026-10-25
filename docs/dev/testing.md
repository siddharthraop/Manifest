# Testing

```sh
ruff check . && ruff format --check manifest api tests && mypy && pytest -q -n 4
```

- **One test file per user story** (`tests/test_us01_report.py` … `test_us07_audiences.py`) and per success metric
  (`test_metrics.py`). A story's acceptance criteria are its assertions.
- **Real tools, real corpus.** Engine tests run DITA-OT, Sphinx and lychee on the TPU map; session fixtures in
  `tests/conftest.py` migrate once and share the result. Tests that need the toolchain are skipped without it.
- **The model is scripted.** `tests/test_agent.py` drives the real agent loop with a scripted model, records it, and
  replays the recording. No key is needed.
- **The cloud is faked at the edges.** `tests/test_api.py` and `tests/test_cloud_job.py` use in-memory versions of
  Firestore, Cloud Storage, Cloud Run and Google sign-in with the same interfaces.
- **The help gate.** `tests/test_help.py` fails if a console help ID has no topic or the API reference is stale. CI also
  builds the help in DITA-OT strict mode and runs MANIFEST on its own help.
- **The container.** CI's `image` job builds the image, migrates inside it, starts the API and checks the size budget.
