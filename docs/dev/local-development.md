# Local development

You need Python 3.11+, Java 17+, [DITA-OT 4.4](https://www.dita-ot.org/download) and
[lychee 0.24](https://github.com/lycheeverse/lychee).

```sh
git clone --recurse-submodules https://github.com/siddharthraop/Manifest.git && cd Manifest
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,sphinx,agent,cloud]'
export DITA_HOME=/path/to/dita-ot-4.4 LYCHEE=/path/to/lychee
```

| Task | Command |
| --- | --- |
| Migrate the sample map | `manifest migrate corpus/tpu/tpu.ditamap --rules rules` |
| Check DITA in seconds | `manifest lint corpus/tpu/tpu.ditamap` |
| Run the agent from a recording | `manifest fix corpus/tpu/tpu.ditamap --mode replay --recording recordings/tpu.jsonl` |
| Run the API against memory | `uvicorn --factory api.main:create_local_app` then open http://127.0.0.1:8000/docs |
| Build the whole site | `./site/build.sh _site && python -m http.server -d _site` |
| Build only the help | `$DITA_HOME/bin/dita -i help/manifest-help.ditamap -f html5 -o build/help --processing-mode=strict` |
| Regenerate the API reference topic | `python help/tools/openapi2dita.py` |

The corpus is a Git submodule pinned to one commit of
[dita-corpus](https://github.com/siddharthraop/dita-corpus), so a run is reproducible.
