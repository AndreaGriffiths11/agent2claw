# Setup Guide

This guide covers the full path from clone to a saved exact-file response. Nothing here installs globally or changes OpenClaw configuration.

## Requirements

This preview has been tested on macOS with:

- Grok Bot configured to use **Mac Shell**, with each command reviewed and approved by you;
- OpenClaw `2026.9.1`, with its Gateway running;
- an existing OpenClaw agent you deliberately choose for this bridge;
- Python `3.9+`, Bash, and SQLite (`python3`, `bash`, and `sqlite3` on `PATH`);
- Git and access to this GitHub repository (the owner may still require permission while it is unpublished).

Check the local prerequisites without invoking a model:

```bash
command -v python3 bash sqlite3 openclaw
openclaw --version
openclaw status
openclaw agents list
```

Do not create a new agent just for the quickstart unless you have separately reviewed its workspace, model, and tool permissions.

## 1. Clone from GitHub

```bash
git clone https://github.com/AndreaGriffiths11/grokbot2claw.git
cd grokbot2claw
```

Until the owner publishes the repository, cloning may require a GitHub account that has been granted access. There is no install step.

## 2. Run the offline test suite

```bash
python3 -m unittest -v test_message.py test_http_server.py test_openclaw_adapter.py
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

A successful run prints the agent's report. The command returns nonzero and prints no partial reply if the bridge or OpenClaw call fails. The selected agent keeps its normal permissions, so the prompt is a task boundary, not a tool sandbox. Review the path and use a least-privileged agent.

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

The file is UTF-8 JSON written through an owner-only temporary file and atomically linked once as `result.json` with mode `0600` inside a new owner-only `0700` run directory. Existing files are never overwritten, and existing operator-owned parent directory modes are not changed. The SHA-256 value covers the exact final `result.json` bytes. `--output-dir` takes precedence over `--json`: the full envelope goes to the file and stdout remains the compact receipt. Without `--output-dir`, stdout behavior is unchanged (`--json` prints the full envelope; otherwise stdout is the plain reply).

The result persists until you delete it. It can contain private prompt-derived content, so keep `results/` out of version control and remove finished runs when they are no longer needed. If Grok Bot cannot read or attach the returned path, move the output directory to an already-approved location; do not widen Grok Bot's permissions for this bridge.

## 5. Let Grok Bot use it through Mac Shell

Give Grok Bot the repository directory and the exact bounded command pattern below. Replace both placeholders yourself:

```text
Working directory: /path/to/grokbot2claw

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
usage: message.py [-h] [--send] --agent ID [--message-file PATH] [--json]
                  [--output-dir DIRECTORY]

  --send                authorize exactly one OpenClaw invocation
  --agent ID            operator-selected OpenClaw agent id
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

The command creates a random bearer token, an owner-only auth file, a temporary SQLite mailbox, a loopback listener on a random port, and a dedicated OpenClaw session key named `grokbot2claw`. It then:

1. validates the agent id and UTF-8 message (maximum 8 KiB);
2. starts the local resources;
3. invokes `openclaw agent --agent … --session-key grokbot2claw --message-file … --json` without `--deliver`;
4. accepts only a successful, bounded UTF-8 text payload; NUL, DEL, and control bytes other than tab, CR, and LF are rejected, while trailing newlines are preserved;
5. prints the reply, or saves the completed response envelope when `--output-dir` is set, and removes the listener, token file, mailbox, temporary prompt/output files, wrappers, and bridge process group.

The `agent:YOUR_AGENT_ID:grokbot2claw` conversation persists. An owner-only local file lock keyed by agent and this fixed session rejects a concurrent command before model invocation and releases automatically on normal exit, failure, or catchable signals. The stable `0600` lock file remains as safe metadata in a `0700` per-user lock directory; it is not unlinked, avoiding lock-inode replacement races. This serialization is local to one machine and this program. A local timeout or interruption may leave work already accepted by the Gateway or provider running, so it is not remote cancellation or cross-machine exclusivity. `SIGKILL` cannot run cleanup.

## Next steps

- [Responsible Use](responsible-use.md) for the security model and its limits.
- [Compatibility](compatibility.md) for what has and has not been verified.
- [Troubleshooting](troubleshooting.md) when a step fails.
