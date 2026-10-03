# Setup Guide

This guide covers the full path from clone to a saved exact-file response. Nothing here installs globally or changes OpenClaw configuration.

## Requirements

This preview has been tested on macOS with:

- Grok Bot configured to use **Mac Shell**, with each command reviewed and approved by you;
- OpenClaw `2026.9.1`, with its Gateway running;
- an existing OpenClaw agent you deliberately choose for this bridge;
- Python `3.9+`, Bash, and SQLite `3.35+` (`python3`, `bash`, and `sqlite3` on `PATH`; the bridge's claim query uses `UPDATE … RETURNING`, and `bridge.sh` exits immediately if the installed `sqlite3` lacks it);
- Git (a normal `git clone` of this repository).

Check the local prerequisites without invoking a model:

```bash
command -v python3 bash sqlite3 openclaw
openclaw --version
openclaw status
openclaw agents list
```

Do not create a new agent just for the quickstart unless you have separately reviewed its workspace, model, and tool permissions.

## Preflight with `--doctor`

Before sending anything, run a local-only preflight check. It never invokes a model, touches the network, or writes outside temporary and lock directories:

```bash
python3 message.py --doctor
```

`--doctor` is a standalone mode: it does not require `--send` or `--agent`. It checks, in order: the Python version, whether `openclaw`, `bash`, and `sqlite3` are on `PATH`, that `adapters/openclaw.sh` and `bridge.sh` exist and are executable, that `runtime.py` and `http_server.py` import cleanly, that a temporary directory can be created and written, and that the local session lock directory can be created and passes its own safety check. Each line reports `ok` or `FAIL`; every `FAIL` line names a concrete fix. The command exits `0` only if every check passes.

Example output when OpenClaw is not yet installed:

```
[ok] python version: python 3.12 >= 3.9
[FAIL] openclaw on PATH: openclaw executable not found on PATH (fix: install OpenClaw per its official documentation, then ensure `openclaw` is on PATH; verify with `command -v openclaw`)
[ok] bash on PATH: bash found at /bin/bash
[ok] sqlite3 on PATH: sqlite3 found at /usr/bin/sqlite3
[ok] adapters/openclaw.sh: adapters/openclaw.sh exists and is executable
[ok] bridge.sh: bridge.sh exists and is executable
[ok] runtime.py import: runtime imports cleanly
[ok] http_server.py import: http_server imports cleanly
[ok] temporary directory: temporary directory can be created and written
[ok] session lock directory: session lock directory /tmp/grokbot2claw-locks-501 is safe
doctor: one or more checks failed; see FAIL lines above for fixes
```

## 1. Clone from GitHub

```bash
git clone https://github.com/AndreaGriffiths11/agent2claw.git
cd agent2claw
```

Anyone can clone the repository with the command above. There is no install step.

## 2. Run the offline test suite

```bash
python3 -m unittest -v test_message.py test_http_server.py test_openclaw_adapter.py test_bridge_privacy.py
python3 runtime.py --replay --agent YOUR_AGENT_ID
```

Replace `YOUR_AGENT_ID` with an id shown by `openclaw agents list`. Replay uses a fake OpenClaw executable and does **not** call a model or provider.

## 3. Ask for a bounded test report

This is the first command that invokes the selected OpenClaw agent:

```bash
python3 message.py --send --agent YOUR_AGENT_ID --message-file - <<'PROMPT'
Inspect /path/to/project and run its documented offline test command. Do not edit files, install dependencies, access credentials, use the network, send messages, or recursively invoke this bridge. Return the exact command, pass/fail/error/skip counts, duration, failures with file lines, and what the run does not prove. If all tests pass, say so; do not invent failures.
PROMPT
```

A successful run prints the agent's report. The command returns nonzero and prints no partial reply if the bridge or OpenClaw call fails. The single stderr line names the failure class without message content: for example `adapter_failed` (OpenClaw returned an error), `bridge_exited` (the local bridge stopped before the reply; run `--doctor` and check that `sqlite3` is present and at least 3.35), `deadline_exceeded` (no reply within the fixed 300-second window), or `session_busy` (another command holds this agent/session). The selected agent keeps its normal permissions, so the prompt is a task boundary, not a tool sandbox. Review the path and use a least-privileged agent.

## 4. Save the exact response for Grok Bot

Long JSON copied through chat can be reformatted or truncated. When exact bytes matter, save the complete response to a file instead of asking Grok Bot to reconstruct it from chat output. Choose a directory that is **already inside Grok Bot's approved Mac Shell access**; this option does not grant or expand filesystem permissions.

From the repository root, this working flow uses the ignored `results/` directory:

```bash
python3 message.py --send --agent YOUR_AGENT_ID --message-file - --output-dir ./results <<'PROMPT'
YOUR_APPROVED_MESSAGE
PROMPT
```

On success, stdout contains only a compact JSON receipt:

```json
{"status":"completed","request_id":"…","result_path":"/absolute/path/results/grokbot2claw-…/result.json","sha256":"…"}
```

`result.json` contains the full completed response envelope:

```json
{"status":"completed","request_id":"…","reply":"the exact agent reply"}
```

The file is UTF-8 JSON written through an owner-only temporary file and atomically linked once as `result.json` with mode `0600` inside a new owner-only `0700` run directory. Existing files are never overwritten. Every directory the command itself creates under `--output-dir` — each missing intermediate component as well as the final directory — is created with mode `0700` regardless of the process umask, so a nested path never leaves a group- or world-readable parent behind. Modes on pre-existing operator-owned parent directories are never changed. A `--output-dir` whose final component is a symlink is rejected outright, and a pre-existing final directory must already be owned by you and owner-only (`0700` or stricter); anything else fails with a clear error instead of silently writing private output into an unsafe location. The SHA-256 value covers the exact final `result.json` bytes. `--output-dir` takes precedence over `--json`: the full envelope goes to the file and stdout remains the compact receipt. Without `--output-dir`, stdout behavior is unchanged (`--json` prints the full envelope; otherwise stdout is the plain reply).

The result persists until you delete it. It can contain private prompt-derived content, so keep `results/` out of version control and remove finished runs when they are no longer needed. If Grok Bot cannot read or attach the returned path, move the output directory to an already-approved location; do not widen Grok Bot's permissions for this bridge.

## 5. Let Grok Bot use it through Mac Shell

Give Grok Bot the repository directory and the exact bounded command pattern below. Replace both placeholders yourself:

```text
Working directory: /path/to/agent2claw

For a task I approve, run exactly one command in Mac Shell using this form:

python3 message.py --send --agent YOUR_AGENT_ID --message-file - --output-dir ./results <<'PROMPT'
YOUR_APPROVED_MESSAGE
PROMPT

Return the compact stdout receipt to me, then read or attach the exact result_path file.
Do not reconstruct the full JSON from chat, retry, start a daemon, change OpenClaw
configuration, or add --deliver unless I explicitly approve that separate action.
```

Review each Mac Shell request. Never put credentials in a message.

## Command reference

```
usage: message.py [-h] [--doctor] [--send] [--agent ID] [--session-key KEY]
                  [--message-file PATH] [--json] [--output-dir DIRECTORY]

  --doctor              run local environment preflight checks and exit (no
                        model, no network)
  --send                authorize exactly one OpenClaw invocation
  --agent ID            operator-selected OpenClaw agent id
  --session-key KEY     operator-selected live OpenClaw session key (default:
                        $GROKBOT2CLAW_SESSION_KEY or 'grokbot2claw')
  --message-file PATH   read UTF-8 message from PATH, or - for stdin
  --json                print the full response as stable JSON
  --output-dir DIRECTORY
                        save full response JSON in a private run directory and
                        print a compact receipt
```

## What happens during one command

```text
Grok Bot → approved Mac Shell command → local Python process
         → authenticated 127.0.0.1 request → temporary SQLite mailbox
         → bridge + fixed OpenClaw adapter → selected OpenClaw agent
         → reply on stdout → Grok Bot
```

The command creates a random bearer token, an owner-only auth file, a temporary SQLite mailbox, a loopback listener on a random port, and a live OpenClaw session key that defaults to `grokbot2claw` but can be overridden per invocation: `--session-key` takes precedence, then the `GROKBOT2CLAW_SESSION_KEY` environment variable, then the `grokbot2claw` default. The value must match the same character rules as `--agent` (letters, digits, underscores, or hyphens). The replay session key stays fixed at `grokbot2claw-replay` and is not configurable. It then:

1. validates the agent id, session key, and UTF-8 message (maximum 8 KiB);
2. starts the local resources;
3. invokes `openclaw agent --agent … --session-key … --message-file … --json` without `--deliver`;
4. accepts only a successful, bounded UTF-8 text payload; NUL, DEL, and control bytes other than tab, CR, and LF are rejected, while trailing newlines are preserved;
5. prints the reply, or saves the completed response envelope when `--output-dir` is set, and removes the listener, token file, mailbox, temporary prompt/output files, wrappers, and bridge process group.

The `agent:YOUR_AGENT_ID:<session key>` conversation persists. An owner-only local file lock keyed by agent and this session key rejects a concurrent command before model invocation and releases automatically on normal exit, failure, or catchable signals. The stable `0600` lock file remains as safe metadata in a `0700` per-user lock directory; it is not unlinked, avoiding lock-inode replacement races. This serialization is local to one machine and this program. A local timeout or interruption may leave work already accepted by the Gateway or provider running, so it is not remote cancellation or cross-machine exclusivity. `SIGKILL` cannot run cleanup.

## Next steps

- [Responsible Use](responsible-use.md) for the security model and its limits.
- [Compatibility](compatibility.md) for what has and has not been verified.
- [Troubleshooting](troubleshooting.md) when a step fails.
