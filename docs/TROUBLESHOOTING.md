# Troubleshooting

## PDF extraction produces no markdown / "marker: skipped"

PDF extraction calls a Marker HTTP service, not a local binary. Make sure
`LIBRARIAN_SPARK_URL` points at a reachable Marker service:

```bash
echo "$LIBRARIAN_SPARK_URL"
curl -s "$LIBRARIAN_SPARK_URL" -o /dev/null -w '%{http_code}\n'
```

## No references/citations/sections extracted / "grobid: skipped"

References, citations, and section headings come from GROBID. Set
`GROBID_BASE_URL` to a reachable GROBID service:

```bash
echo "$GROBID_BASE_URL"
curl -s "$GROBID_BASE_URL/api/isalive"
```

A 200 OK carrying a non-TEI body (e.g. an HTML error/queue page) now raises a
clear error rather than silently producing empty artifacts.

## “ModuleNotFoundError: markdownify”

EPUB extraction uses `markdownify`. Install it in your venv:

```bash
pip install markdownify
```

## Ollama errors (classify/ask)

The `classification` section in `config/settings.yaml` is also used by `librarian-ask` for synthesis.

Common checks:

```bash
ollama --version
curl -s http://localhost:11434/api/tags
```

If the model configured in `config/settings*.yaml` isn’t present, pull it with Ollama and retry.

## Kindle ingestion / DRM

See `docs/AGENT_CONTEXT.md` for current operational context and
`docs/kindle-screenshot-capture.md` for Kindle capture workflow notes.
