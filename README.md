# Agent2Claw

Send one message from your coding agent to your OpenClaw agent and get the reply back.

**Unofficial local-first developer preview.** Not affiliated with or endorsed by xAI, Grok, Cursor, Anysphere, OpenClaw, or Meta.

## Explainer video

A one-minute walkthrough of what the bridge does, how the parts fit, and the privacy decision under the hood. Captions in English.

<video src="https://github.com/user-attachments/assets/107010b5-0d19-4ddb-97a2-e7f80ab6653f" controls muted playsinline width="720">
  Your browser does not support HTML5 video. <a href="https://github.com/user-attachments/assets/107010b5-0d19-4ddb-97a2-e7f80ab6653f">Download the explainer</a>.
</video>

## Quick Start

```bash
git clone https://github.com/AndreaGriffiths11/agent2claw.git
cd agent2claw
python3 -m unittest -v test_message.py test_http_server.py test_openclaw_adapter.py test_bridge_privacy.py
python3 runtime.py --replay --agent YOUR_AGENT_ID
```

Replace `YOUR_AGENT_ID` with an id from `openclaw agents list`. The test suite and replay use fixtures and call no model or provider. There is no install step and no global configuration change.

**Requirements:** macOS with Grok Bot using Mac Shell, OpenClaw `2026.9.1` with its Gateway running, an existing OpenClaw agent you choose, and `python3` (3.9+), `bash`, and `sqlite3` (3.35+) on `PATH`. See [Setup](docs/setup.md) for the full checklist.

## What It Does

Your coding agent plans work in its own conversation. OpenClaw acts inside the agent environment you already configured. Agent2Claw is the explicit handoff between them:

- one approved shell command sends one message;
- the operator chooses the OpenClaw agent, not the message sender;
- the reply comes back on stdout, or as exact JSON saved to a directory Grok Bot can already read;
- no server remains running afterwards.

```text
Grok Bot → approved Mac Shell command → local Python process
         → authenticated 127.0.0.1 request → temporary SQLite mailbox
         → bridge + fixed OpenClaw adapter → selected OpenClaw agent
         → reply on stdout → Grok Bot
```

## Connect Grok Bot

Using a different operator? The bridge is sender-agnostic: [operating it from Muse or any agent with SSH access](docs/muse.md).

First live send, from the repository root:

```bash
python3 message.py --send --agent YOUR_AGENT_ID --message-file - <<'PROMPT'
Inspect /path/to/project and run its documented offline test command. Do not edit files, install dependencies, access credentials, use the network, send messages, or recursively invoke this bridge. Return the exact command, pass/fail/error/skip counts, duration, failures with file lines, and what the run does not prove. If all tests pass, say so; do not invent failures.
PROMPT
```

When exact bytes matter, add `--output-dir ./results`. stdout then carries a compact receipt with `result_path` and `sha256`, and the full response is written once to an owner-only `result.json`. Choose a directory already inside Grok Bot's approved Mac Shell access; this flag does not grant permissions.

Give Grok Bot the repository directory and this command pattern, with both placeholders filled in by you:

```text
python3 message.py --send --agent YOUR_AGENT_ID --message-file - --output-dir ./results <<'PROMPT'
YOUR_APPROVED_MESSAGE
PROMPT
```

Review each Mac Shell request. Never put credentials in a message. Full walkthrough, receipt format, and the exact instruction text for Grok Bot are in [Setup](docs/setup.md).

## Security

- **The chosen OpenClaw agent keeps its normal permissions.** This bridge is not a tool sandbox; use a dedicated, least-privileged agent.
- **The sender cannot choose** the agent, session, executable, CLI flags, or delivery route.
- **Loopback only.** The HTTP server binds to `127.0.0.1` with a fresh bearer token and lives for one command.
- **No daemon, retry loop, web UI, or MCP server.** Every run requires `--send`.
- **One local command per agent session.** An owner-only cross-process lock rejects overlap for the fixed persistent session before OpenClaw is invoked. It does not coordinate other machines or guarantee that timed-out provider work has stopped.
- **No identity proof for "Grok."** The principal label records the local workflow, not an authenticated Grok account.

Details and guardrails: [Responsible Use](docs/responsible-use.md). To report a vulnerability, follow [SECURITY.md](SECURITY.md) and do not open a public issue.

## Docs

| | |
|---|---|
| [Setup](docs/setup.md) | Requirements, clone, offline tests, first live send, `--output-dir`, Grok Bot instructions |
| [Muse and other agents](docs/muse.md) | Operating the bridge from Muse or any agent with SSH access to the Mac |
| [Responsible Use](docs/responsible-use.md) | Security model, limits, agent permissions, guardrails |
| [Compatibility](docs/compatibility.md) | What has been verified, where, and what remains unverified |
| [Troubleshooting](docs/troubleshooting.md) | OpenClaw, replay, and file output problems |
| [Contributing](CONTRIBUTING.md) | Branches, tests, PR expectations |
| [Privacy](PRIVACY.md) | What the bridge stores locally and what it never sends |
| [Changelog](CHANGELOG.md) | Notable changes in this preview |

## Project status

Unofficial developer preview. This repository is not packaged, deployed, or installed globally. Verification history is in [Compatibility](docs/compatibility.md).

## License and provenance

New project material and modifications are licensed under the [Apache License 2.0](LICENSE). Reused MIT-licensed code retains its original notices under [`THIRD_PARTY_LICENSES/`](THIRD_PARTY_LICENSES/). See [NOTICE](NOTICE) and [PROVENANCE.md](PROVENANCE.md) for the exact source boundary and attribution.
