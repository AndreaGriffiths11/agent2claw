# Changelog

GrokBot2Claw is a developer preview with no tagged releases. Entries are grouped by date from the repository's Git history. Dates are in 2026.

## Unreleased

## v0.1.0-preview — 2026-09-27

First public developer preview.

- one-message local bridge from Grok Bot Mac Shell to a chosen OpenClaw agent
- doctor mode, secure `--output-dir`, session lock, prompt-file privacy (no message text on argv)
- docs for setup, responsible use, compatibility, troubleshooting
- CI on Linux (Python 3.9) and macOS (Python 3.13); Apache-2.0
- docs: public-clone wording; CI hardening for macOS session-lock flake and SQLite ResourceWarnings

### 2026-09-20
- docs: reorganize documentation into `docs/` (setup, responsible use, compatibility, troubleshooting), thin the README, and add SECURITY.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, PRIVACY.md, and this changelog
- docs: describe the repository as a shareable developer preview instead of a private staging preview

### 2026-09-16
- fix: handle denied macOS cleanup probes
- feat: add exact file output mode (`--output-dir`), writing `result.json` with mode `0600` in a `0700` run directory and printing a compact receipt with a SHA-256 hash

### 2026-09-15
- fix: allow a replay cleanup bound on slow runners
- chore: update CI actions to Node 24 releases
- ci: add cross-platform fixture CI (Linux/Python 3.9, macOS/Python 3.13)

### 2026-09-14
- docs: document the verified useful-task handoff (live send to the `rusty` agent returning a real 35-test report)
- docs: document staging compatibility accurately
- initial private staging release: one-message Grok Bot to OpenClaw bridge adapted from `agmsg-bridge`; see [PROVENANCE.md](PROVENANCE.md)
