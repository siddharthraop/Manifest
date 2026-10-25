# Recordings

One JSONL file per DITA map, named after the map (`tpu.jsonl` for `tpu.ditamap`), written by a
live run:

```sh
GOOGLE_API_KEY="$(gcloud secrets versions access latest --secret=gemini-api-key)" \
  manifest fix corpus/tpu/tpu.ditamap --mode live --recording recordings/tpu.jsonl
```

Each line is one Gemini response (`n`, `model`, `response`). `manifest fix --mode replay` and the
Cloud Run job's `replay` mode play the file back: the same agent, tools and checks run for real,
only the model's words are fixed, and no key or quota is used. A run that diverges from its
recording stops with an error. See ADR 0007.

Only public sample content goes to the Gemini API free tier, because free-tier content may be used
to improve Google products.
