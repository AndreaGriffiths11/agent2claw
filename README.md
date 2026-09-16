# GrokBot2Claw

Send a message from Grok Bot to your OpenClaw agent and return the reply through approved Mac Shell access.

**Unofficial local-first developer preview.** Not affiliated with or endorsed by xAI, Grok, Cursor, Anysphere, or OpenClaw.

## Why use it?

Grok Bot can plan work in its desktop conversation, while OpenClaw can act inside the agent environment you already configured. GrokBot2Claw gives the two a small, explicit handoff:

- one approved shell command sends one message;
- the operator chooses the OpenClaw agent, not the message sender;
- the reply can come back on stdout or be saved as exact JSON in a directory Grok Bot can already access;
- no server remains running and no global configuration changes.

Use it when Grok Bot needs a bounded answer from an existing OpenClaw agent without manually copying the task and reply between apps.

## Requirements

This preview has been tested on macOS with:

- Grok Bot configured to use **Mac Shell**, with each command reviewed and approved by you;
- OpenClaw `2026.9.1`, with its Gateway running;
- an existing OpenClaw agent you deliberately choose for this bridge;
- Python `3.9+`, Bash, and SQLite (`python3`, `bash`, and `sqlite3` on `PATH`);
- Git and GitHub access to this private staging repository.

Check the local prerequisites without invoking a model:

```bash
command -v python3 bash sqlite3 openclaw
openclaw --version
openclaw status
openclaw agents list
```

Do not create a new agent just for the quickstart unless you have separately reviewed its workspace, model, and tool permissions.

## Quickstart

### 1. Clone the private preview

```bash
git clone https://github.com/AndreaGriffiths11/grokbot2claw.git
cd grokbot2claw
```

The private staging repository requires an account with access. There is no install step.

### 2. Run the offline test suite

```bash
python3 -m unittest -v test_message.py test_http_server.py test_openclaw_adapter.py
python3 runtime.py --replay --agent YOUR_AGENT_ID
```

Replace `YOUR_AGENT_ID` with an id shown by `openclaw agents list`. Replay uses a fake OpenClaw executable and does **not** call a model or provider.

### 3. Ask for a bounded test report

This is the first command that invokes the selected OpenClaw agent:

```bash
python3 message.py --send --agent YOUR_AGENT_ID --message-file - <<'PROMPT'
Inspect /path/to/project and run its documented offline test command. Do not edit files, install dependencies, access credentials, use the network, send messages, or recursively invoke this bridge. Return the exact command, pass/fail/error/skip counts, duration, failures with file lines, and what the run does not prove. If all tests pass, say so; do not invent failures.
PROMPT
```

A successful run prints the agent's report. The command returns nonzero and prints no partial reply if the bridge or OpenClaw call fails. The selected agent keeps its normal permissions, so the prompt is a task boundary, not a tool sandbox. Review the path and use a least-privileged agent.

### 4. Save the exact response for Grok Bot

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

The file is UTF-8 JSON written once with owner-only mode `0600` inside a new owner-only `0700` run directory, so existing files are never overwritten. The SHA-256 value covers the exact `result.json` bytes. `--output-dir` takes precedence over `--json`: the full envelope goes to the file and stdout remains the compact receipt. Without `--output-dir`, stdout behavior is unchanged (`--json` prints the full envelope; otherwise stdout is the plain reply).

The result persists until you delete it. It can contain private prompt-derived content, so keep `results/` out of version control and remove finished runs when they are no longer needed. If Grok Bot cannot read or attach the returned path, move the output directory to an already-approved location; do not widen Grok Bot's permissions for this bridge.

### 5. Let Grok Bot use it through Mac Shell

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
4. accepts only a successful, bounded text payload;
5. prints the reply, or saves the completed response envelope when `--output-dir` is set, and removes the listener, token file, mailbox, temporary prompt/output files, wrappers, and bridge process group.

The `agent:YOUR_AGENT_ID:grokbot2claw` conversation persists. Stopping locally may not cancel computation already accepted upstream.

## Security model and limits

- **The chosen OpenClaw agent keeps its normal permissions.** This project does not create a tool sandbox. Use a dedicated, least-privileged agent if the connected workflow handles untrusted requests.
- **The sender cannot choose the agent, session, executable, CLI flags, or delivery route.** Those are fixed by the local command and environment.
- **No external listener is exposed.** The HTTP server binds only to `127.0.0.1`, uses a fresh in-memory bearer value, and exists for one command.
- **Every run requires `--send`.** There is no background daemon, retry loop, always-on peer, web UI, or MCP server.
- **Mac Shell approval remains the control point.** Treat requests from a model as untrusted input and approve only bounded tasks you understand.
- **No identity proof for “Grok.”** The internal principal label records that the command came through this local workflow; it is not cryptographic authentication of a Grok account or bot.
- **Chat output is not a byte-preserving file transport.** A prior response became garbled after valid JSON left this command, but the exact corruption point was not established. Use `--output-dir` and verify the receipt hash when exact output matters.

## Troubleshooting

**`openclaw executable not found`**  
Run `command -v openclaw`. Install or repair OpenClaw using its official documentation; this repository does not modify global installations.

**Unknown agent or Gateway error**  
Run `openclaw status` and `openclaw agents list`, then repeat the command with an existing agent id. Authentication and provider setup remain owned by OpenClaw.

**The command times out or is interrupted**  
The local listener and temporary files are cleaned up. A provider request already accepted by the Gateway may continue remotely; inspect the dedicated OpenClaw session before retrying.

**Replay passes but live use fails**  
Replay proves the local HTTP, mailbox, bridge, adapter, parser, and cleanup path with fixtures. It does not prove provider credentials, model availability, Grok Bot Mac Shell approval, or a fresh-machine setup.

## Compatibility status

- macOS arm64: fixture suite passed against Python 3.9.6, Bash 3.2.57, SQLite 3.51.0, and the OpenClaw 2026.9.1 CLI contract.
- Private GitHub Actions CI runs the fixture suite and synthetic replay on Linux/Python 3.9 and macOS/Python 3.13. It invokes no agent, model, or provider and receives no repository secrets.
- CI is fixture-level portability evidence, not proof of a live Linux OpenClaw installation.
- Local live handoff verified on September 14, 2026: `message.py --send` invoked the configured `rusty` agent through a dedicated OpenClaw session. The agent ran the repository's read-only fixture suite and returned an actual report: 35 passed, 0 failed, 0 errors, and 0 skipped in 23.988 seconds. The repository remained unchanged.
- Built-in file output was live-verified from the local command line on September 16, 2026: the saved response matched the expected reply, its SHA-256 matched the receipt, and the file and run directory modes were `0600` and `0700`. Fixture coverage passed 39 tests in 21.343 seconds.
- The September 16 file-output check started from the local command line, not the Grok Bot UI. The earlier private capture proves Grok-initiated exact-file handling with the wrapper that motivated this built-in option; it does not prove that Grok Bot has run the new flag. Fresh-machine setup and a live Linux OpenClaw installation also remain unverified.

## Project status

This repository is a **private staging preview**. It has not been published publicly, packaged, deployed, or installed globally.

## License and provenance

New project material and modifications are licensed under the [Apache License 2.0](LICENSE). Reused MIT-licensed code retains its original notices under [`THIRD_PARTY_LICENSES/`](THIRD_PARTY_LICENSES/). See [NOTICE](NOTICE) and [PROVENANCE.md](PROVENANCE.md) for the exact source boundary and attribution.
