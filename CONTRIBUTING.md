# Contributing to GrokBot2Claw

Thanks for wanting to contribute. GrokBot2Claw is a small, bounded bridge; changes should keep it that way.

## Quick Start

1. Fork the repo
2. Clone your fork
3. Create a branch from `main`:
   ```bash
   git checkout -b fix/your-thing
   ```
4. Run the offline suite before and after your change:
   ```bash
   python3 -m unittest -v test_message.py test_http_server.py test_openclaw_adapter.py
   python3 runtime.py --replay --agent ci-agent
   ```
5. Open a PR against `main`

There are no dependencies to install. Python `3.9+`, Bash, and SQLite on `PATH` are enough. The suite and replay use fixtures and never call a model or provider.

## Branch Naming

- `feat/` for new behavior
- `fix/` for bug fixes
- `docs/` for documentation only
- `refactor/` for code changes that do not add features or fix bugs

## PR Guidelines

- Keep PRs focused: one feature or fix per PR
- Make sure CI's checks pass locally: `python3 -m py_compile *.py`, `bash -n bridge.sh adapters/openclaw.sh`, the unittest suite, and the replay
- Describe what changed and why
- If the change touches live behavior that fixtures cannot cover, say what you ran by hand and what remains unverified. Do not report a live run you did not do.
- Update [docs/compatibility.md](docs/compatibility.md) if you add verification evidence

## Code Style

- Python standard library only; Bash 3.2 compatible shell
- No new dependencies unless discussed first in an issue
- Keep the one-command, loopback-only, no-daemon model. Changes that add a persistent server, retry loop, delivery route, or a way for the message sender to choose the agent need discussion before code.

## Layout

```
message.py               # command entry point
runtime.py               # local runtime and --replay
http_server.py           # loopback HTTP server, bearer auth
bridge.sh                # temporary SQLite mailbox and bridge process
adapters/openclaw.sh     # fixed OpenClaw adapter
test_*.py                # fixture suite
docs/                    # setup, responsible use, compatibility, troubleshooting
```

## What to Avoid

- Do not commit secrets, tokens, `results/`, or `state/`
- Do not widen what the message sender can control
- Do not add `--deliver` or any always-on component
- Do not change reused MIT-licensed files without preserving their notices; see [PROVENANCE.md](PROVENANCE.md)

## Security Issues

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).

## Need Help?

Open an issue with the exact command (placeholders redacted) and the output. Leave out credentials and message content.
