# Privacy Policy: Agent2Claw

**Last updated:** September 25, 2026

Agent2Claw is a local command-line bridge. It runs on your machine, talks only to itself over loopback and to your local OpenClaw CLI, and keeps no service running between commands.

---

## What it processes

For each `message.py --send` command the bridge handles:

- **The agent id** you pass with `--agent`
- **The message text** you pass with `--message-file` (UTF-8, 8 KiB maximum)
- **The reply** returned by the OpenClaw agent

All three travel through a temporary SQLite mailbox and a loopback HTTP listener on `127.0.0.1`, then into `openclaw agent` on the same machine.

## What it stores

**During a command**, inside a fresh temporary directory created with owner-only permissions:

- an `auth.json` file holding the SHA-256 of a random bearer token, mode `0600`
- a `messages.db` SQLite mailbox
- prompt files passed between the bridge, the adapter, and OpenClaw, mode `0600`
- a small JSON record of the OpenClaw CLI result (run id, status, session id, model, provider, tool summary) used to build the reply
- dispatch counters, temporary reply files, and wrapper scripts used by the bridge

The command removes this directory, the listener, and the bridge process group when it exits, including on failure or catchable interruption (`SIGHUP`, `SIGINT`, or `SIGTERM`). `SIGKILL` cannot be handled. A provider request already accepted upstream may continue after local cleanup.

**After a command**, only if you pass `--output-dir`:

- one `result.json` containing the full response envelope, written once with mode `0600` inside a new `0700` run directory under the directory you chose

This file persists until you delete it. It can contain private prompt-derived content. Keep the output directory out of version control (`results/` is ignored by default) and remove finished runs.

**Outside this repository**, OpenClaw keeps its own session history. Each command uses a live session key that defaults to `grokbot2claw` but can be overridden with `--session-key` or `GROKBOT2CLAW_SESSION_KEY`, so the conversation `agent:YOUR_AGENT_ID:<session key>` persists in OpenClaw's storage under OpenClaw's rules. Anything in a message may remain there.

The bridge also keeps one lock file per local agent/session. It lives in an owner-only `grokbot2claw-locks-USER_ID` directory under a secure `XDG_RUNTIME_DIR`, or the platform temporary directory otherwise. The directory is `0700`; lock files are `0600` and contain only the agent id, session key, and current process id. Lock files persist so every process locks the same inode; they are never unlinked during normal operation. Synthetic replay uses a distinct session key and a lock inside its temporary run directory, so replay cannot collide with a live session.

## What it does not do

- No analytics, telemetry, or phone-home behavior
- No outbound network requests from the bridge itself; the only network use is loopback `127.0.0.1`
- No message snippets in bridge diagnostics and no log files: this project does not log prompt or reply bodies, the normal bridge process's diagnostic output is discarded, and nothing is appended to a persistent log. OpenClaw and its provider remain governed by their own logging policies.
- No message text on process command lines: the prompt travels in private (`0600`) files between the bridge, the adapter, and OpenClaw, never as a command-line argument, so other local users cannot read it with `ps`.
- No global configuration changes to Grok Bot, OpenClaw, or your shell
- No collection of Grok account data; the principal label records that a command came through this workflow and is not an identity check

## Third parties

The bridge sends your message to the OpenClaw agent you selected. What OpenClaw and its configured model provider do with that message is governed by their own terms and by how you configured the agent. Agent2Claw does not send data to any other party.

## Contact

Questions: open an issue at [github.com/AndreaGriffiths11/agent2claw](https://github.com/AndreaGriffiths11/agent2claw/issues). Security concerns: follow [SECURITY.md](SECURITY.md) instead.
