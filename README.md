# GrokBot2Claw

Send a message from Grok Bot to your OpenClaw agent and return the reply through approved Mac Shell access.

**Unofficial, local-first developer preview.** GrokBot2Claw is not affiliated with or endorsed by xAI, Grok, Cursor, Anysphere, or OpenClaw.

## Why use it?

Grok Bot can plan work in its desktop conversation, while OpenClaw can act inside the agent environment you already configured. GrokBot2Claw gives the two a small, explicit handoff:

- one approved shell command sends one message;
- the operator chooses the OpenClaw agent, not the message sender;
- the reply comes back on stdout, so Grok Bot can read it in the same Mac Shell run;
- no server is left running, no MCP server is installed, and no global configuration is changed.

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

The repository is private during staging, so cloning requires an account that already has access. There is no installer and no package dependency step.

### 2. Run the offline test suite

```bash
python3 -m unittest -v test_message.py test_http_server.py test_openclaw_adapter.py
python3 runtime.py --replay --agent YOUR_AGENT_ID
```

Replace `YOUR_AGENT_ID` with an id shown by `openclaw agents list`. The replay uses a fake OpenClaw executable and does **not** call a model or provider.

### 3. Ask for a bounded test report

This is the first command that invokes the selected OpenClaw agent:

```bash
python3 message.py --send --agent YOUR_AGENT_ID --message-file - <<'PROMPT'
Inspect /path/to/project and run its documented offline test command. Do not edit files, install dependencies, access credentials, use the network, send messages, or recursively invoke this bridge. Return the exact command, pass/fail/error/skip counts, duration, failures with file lines, and what the run does not prove. If all tests pass, say so; do not invent failures.
PROMPT
```

A successful run prints the agent's report. The command returns nonzero and prints no partial reply if the bridge or OpenClaw call fails. The selected agent keeps its normal permissions, so the prompt is a task boundary, not a tool sandbox. Review the path and use a least-privileged agent.

### 4. Let Grok Bot use it through Mac Shell

Give Grok Bot the repository directory and the exact bounded command pattern below. Replace both placeholders yourself:

```text
Working directory: /path/to/grokbot2claw

For a task I approve, run exactly one command in Mac Shell using this form:

python3 message.py --send --agent YOUR_AGENT_ID --message-file - <<'PROMPT'
YOUR_APPROVED_MESSAGE
PROMPT

Return stdout to me. Do not retry, start a daemon, change OpenClaw configuration,
or add --deliver unless I explicitly approve that separate action.
```

Review each Mac Shell request in the Grok Bot UI. Do not place passwords, API keys, bearer tokens, or other credentials in a message.

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
5. prints the reply and removes the listener, token file, mailbox, prompt/output files, wrappers, and bridge process group.

The OpenClaw conversation for `agent:YOUR_AGENT_ID:grokbot2claw` is persistent. Stopping the local command cleans up local processes, but it may not cancel provider-side computation already accepted upstream.

## Security model and limits

- **The chosen OpenClaw agent keeps its normal permissions.** This project does not create a tool sandbox. Use a dedicated, least-privileged agent if the connected workflow handles untrusted requests.
- **The sender cannot choose the agent, session, executable, CLI flags, or delivery route.** Those are fixed by the local command and environment.
- **No external listener is exposed.** The HTTP server binds only to `127.0.0.1`, uses a fresh in-memory bearer value, and exists for one command.
- **Every run requires `--send`.** There is no background daemon, retry loop, always-on peer, web UI, or MCP server.
- **Mac Shell approval remains the control point.** Treat requests from a model as untrusted input and approve only bounded tasks you understand.
- **No identity proof for “Grok.”** The internal principal label records that the command came through this local workflow; it is not cryptographic authentication of a Grok account or bot.

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
- Linux: not yet validated in this staging repository. No cross-platform claim is made.
- Local live handoff verified on September 14, 2026: `message.py --send` invoked the configured `rusty` agent through a dedicated OpenClaw session. The agent ran the repository's read-only fixture suite and returned an actual report: 35 passed, 0 failed, 0 errors, and 0 skipped in 23.988 seconds. The repository remained unchanged.
- That task started from the local command line, not the Grok Bot UI. Grok initiation of this specific task, Mac Shell approval for it, fresh-machine setup, and cross-platform behavior remain unverified.

## Project status

This repository is a **private staging preview**. It has not been published publicly, packaged, deployed, or installed globally.

## License and provenance

New project material and modifications are licensed under the [Apache License 2.0](LICENSE). Reused MIT-licensed code retains its original notices under [`THIRD_PARTY_LICENSES/`](THIRD_PARTY_LICENSES/). See [NOTICE](NOTICE) and [PROVENANCE.md](PROVENANCE.md) for the exact source boundary and attribution.
