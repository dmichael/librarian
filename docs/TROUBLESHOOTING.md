# Troubleshooting

## “calibredb: command not found” / “ebook-convert: command not found”

Calibre’s CLI tools aren’t installed (or aren’t on PATH). Install Calibre and confirm:

```bash
calibredb --version
ebook-convert --version
```

## “marker_single not found”

Install the PDF extraction dependency:

```bash
pip install marker-pdf
marker_single --help
```

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
