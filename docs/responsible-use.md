# Using Agent2Claw Responsibly

Agent2Claw hands one approved message from Grok Bot to an OpenClaw agent that already exists on your machine and returns the reply. The bridge itself is small and bounded. The agent on the other end is not: it keeps every permission you gave it when you configured it in OpenClaw.

Use this bridge when Grok Bot needs a bounded answer from an OpenClaw agent you already trust. Do not treat it as a sandbox, a permission system, or a way to run unattended work.

## When Agent2Claw is the right tool

Use it when:

- Grok Bot plans a task in conversation and an existing OpenClaw agent should carry out a bounded piece of it;
- you want a single reviewed Mac Shell command per handoff, with no server left running;
- the reply must come back exactly, either on stdout or as a saved JSON file with a SHA-256 receipt;
- you want to choose the agent yourself, rather than letting the message sender choose.

## When something else is better

| Use case | Better option | Why |
|---|---|---|
| Interactive back-and-forth with an agent | OpenClaw's own CLI or UI | This bridge sends one message per command and does not stream. |
| Unattended or scheduled automation | OpenClaw scheduling or a purpose-built job runner | Every run here requires a human-approved `--send`. |
| Isolating an untrusted task from your files and tools | A dedicated least-privileged OpenClaw agent, or a separate machine | The bridge cannot restrict what the agent can do. |
| Messages larger than 8 KiB or binary payloads | A file handoff you design yourself | The message is validated as UTF-8 text with an 8 KiB limit. |

## Security model and limits

- **The chosen OpenClaw agent keeps its normal permissions.** This project does not create a tool sandbox. Use a dedicated, least-privileged agent if the connected workflow handles untrusted requests.
- **The sender cannot choose the agent, session, executable, CLI flags, or delivery route.** Those are fixed by the local command and environment.
- **No external listener is exposed.** The HTTP server binds only to `127.0.0.1`, uses a fresh in-memory bearer value, and exists for one command.
- **Every run requires `--send`.** There is no background daemon, retry loop, always-on peer, web UI, or MCP server.
- **Mac Shell approval remains the control point.** Treat requests from a model as untrusted input and approve only bounded tasks you understand.
- **No identity proof for "Grok."** The internal principal label records that the command came through this local workflow; it is not cryptographic authentication of a Grok account or bot.
- **Session continuity is intentional and locally serialized.** Commands for one agent reuse `agent:YOUR_AGENT_ID:grokbot2claw`. A per-user file lock rejects another local Agent2Claw command for that agent/session before invocation. It does not cover other hosts, direct OpenClaw use, or provider-side work already accepted.
- **Chat output is not a byte-preserving file transport.** A prior response became garbled after valid JSON left this command, but the exact corruption point was not established. Use `--output-dir` and verify the receipt hash when exact output matters.

## Guardrails

### Pick a least-privileged agent

Create or select an OpenClaw agent whose workspace, model, and tool permissions you have reviewed for this purpose. Do not point the bridge at an agent that has broad file, network, or credential access unless every task you approve deserves that access.

### Keep a human on every command

Grok Bot must run `message.py` through Mac Shell, and Mac Shell asks you to approve each command. Read the agent id, the prompt, and the output directory before approving. Refuse commands that add `--deliver`, change OpenClaw configuration, start a daemon, or retry on their own.

### Write bounded prompts

State the task, the path, and what the agent must not do. The prompt in [Setup](setup.md) step 3 is a working example: it names the directory, forbids edits, installs, credentials, network use, messaging, and recursive bridge calls, and asks for a report that says what the run does not prove.

The prompt is a task boundary that the agent may or may not honor. It is not enforced by the bridge.

### Treat results as sensitive

Saved `result.json` files and stdout replies can contain prompt-derived content, file paths, and whatever the agent chose to include. Keep `results/` out of version control, store output only in directories Grok Bot already has access to, and delete finished runs.

### Never put credentials in a message

Messages travel through a temporary local mailbox and into the OpenClaw session `agent:YOUR_AGENT_ID:grokbot2claw`, which persists after the command exits. Anything you type into a message may remain in that session history.

### Expect remote work to outlive local cancellation

`SIGHUP`, `SIGINT`, and `SIGTERM` route through bounded local cleanup of the listener, bridge process group, lock, and temporary files. `SIGKILL` cannot be handled. A provider request already accepted by the OpenClaw Gateway may continue even after the local CLI process is stopped. Check the dedicated session before retrying; do not treat lock release as proof of remote cancellation.

## Reporting a problem

Security issues: follow [SECURITY.md](../SECURITY.md) and do not open a public issue. Everything else: see [Troubleshooting](troubleshooting.md) first, then open an issue with credentials and message content removed.
