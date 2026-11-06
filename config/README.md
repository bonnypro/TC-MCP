# Configuration

## `automation.toml`

Central configuration for the F2 automation layer. Edit this file instead of
scattering timeouts and policy constants through code.

Sections:

- `[dialog]` - watchdog interval, operation timeout, unknown-dialog behavior
- `[[dialog.allow]]` / `[[dialog.deny]]` - reviewed content-aware dialog rules
- `[resources]` - LLM/CPU/memory/disk limits and task timeout
- `[security]` - write root, local ADS allowlist, safety path policy, command
  gate
- `[session]` - template, platform, configuration, ADS defaults, output roots

The dialog watchdog is active only while an XAE operation is running: one check
at start, then every `dialog.watchdog_interval_s` seconds (default 10). There is
no idle polling.

`load_config()` in `automation/core/config.py` parses and validates this file.
`DialogPolicy` in `automation/security/dialog_policy.py` applies the allow/deny
rules.

## `sources.json`

The source registry used by `/tc-sources` and `/tc-update`. It contains the official
Beckhoff PDF catalog, GitHub metadata sources, and local package checks. Technical
documentation is PDF-only; web crawling is not part of the pipeline.

## PDF store

- PDFs: `../data/pdfs/`
- Extracted text: `../data/official_text/`
- Versions: `../data/pdf_versions.json`
- Update check: `../data/update_state.json`

Each PDF record in `sources.json` has `id`, `kind: pdf`, `url`, `product`, and the
last extracted `version`. `/tc-update` compares ETag/Last-Modified/Content-Length and
downloads only changed files.

## Removed sources

InfoSys HTML, Virtual Academy HTML/PDF assets, and the local `参考文档` corpus are
not part of the knowledge base.
