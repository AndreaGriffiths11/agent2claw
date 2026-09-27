# Troubleshooting

Each command is self-contained: it creates its own token, mailbox, and loopback listener and removes them on exit. Most failures come from the surrounding environment rather than from leftover state.

## OpenClaw issues

| Problem | Fix |
|---------|-----|
| `openclaw executable not found` | Run `command -v openclaw`. Install or repair OpenClaw using its official documentation; this repository does not modify global installations. |
| Unknown agent or Gateway error | Run `openclaw status` and `openclaw agents list`, then repeat the command with an existing agent id. Authentication and provider setup remain owned by OpenClaw. |
| The command times out or is interrupted | The local listener and temporary files are cleaned up. A provider request already accepted by the Gateway may continue remotely; inspect the dedicated `agent:YOUR_AGENT_ID:grokbot2claw` session before retrying. |

## Test and replay issues

| Problem | Fix |
|---------|-----|
| Replay passes but live use fails | Replay proves the local HTTP, mailbox, bridge, adapter, parser, and cleanup path with fixtures. It does not prove provider credentials, model availability, Grok Bot Mac Shell approval, or a fresh-machine setup. Work through [Setup](setup.md) step 3 by hand. |
| `python3`, `bash`, or `sqlite3` missing | All three must be on `PATH`. Check with `command -v python3 bash sqlite3`. |

## File output issues

| Problem | Fix |
|---------|-----|
| Grok Bot cannot read or attach `result_path` | Move `--output-dir` to a directory already inside Grok Bot's approved Mac Shell access. Do not widen Grok Bot's permissions for this bridge. |
| JSON reply looks garbled in chat | Chat output is not a byte-preserving transport. Use `--output-dir` and compare the receipt `sha256` against the saved `result.json`. |
| Old run directories accumulate | Runs under `results/` persist until you delete them and can contain private prompt-derived content. Remove finished runs and keep `results/` ignored by Git. |

## Still stuck

Open an issue with the exact command (placeholders redacted), the nonzero exit output, and your `openclaw --version`. Do not include credentials or message content. For anything security related, follow [SECURITY.md](../SECURITY.md) instead of filing a public issue.
