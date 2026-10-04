# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Agent2Claw, **please report it responsibly**.

**Do NOT open a public GitHub issue, discussion, or pull request for security vulnerabilities.**

Instead, email: **andreagriffiths11@gmail.com**

If GitHub private vulnerability reporting is enabled for this repository, you may also use the **Security** tab and **Report a vulnerability**, or draft an advisory at https://github.com/AndreaGriffiths11/agent2claw/security/advisories/new. Email is the primary channel.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

Do not include credentials, tokens, or private prompt-derived content from `results/` in the report.

## Response Timeline

- **Acknowledgment**: Within 48 hours
- **Initial assessment**: Within 1 week
- **Fix or mitigation**: As soon as possible, depending on severity

## Scope

Agent2Claw is a local bridge between Grok Bot Mac Shell and an OpenClaw agent. This policy covers:
- The command entry point (`message.py`) and runtime (`runtime.py`)
- The loopback HTTP server and bearer token handling (`http_server.py`)
- The temporary SQLite mailbox and bridge process (`bridge.sh`)
- The fixed OpenClaw adapter (`adapters/openclaw.sh`)
- The `--output-dir` file output mode, including file and directory permissions
- The GitHub Actions workflow (`.github/workflows/`)

Out of scope for this project, though reports are still welcome as context:
- The permissions and behavior of the OpenClaw agent you select. The agent keeps its normal permissions; this bridge does not create a tool sandbox.
- Grok Bot, Mac Shell approval prompts, OpenClaw, or provider services themselves.
- Outcomes that require the operator to approve a Mac Shell command they did not review.

## Security Design

- **Loopback only**: the HTTP server binds to `127.0.0.1` on a random port for one command
- **Fresh bearer token** per run, stored in an owner-only auth file
- **Fixed agent, session, executable, and CLI flags**: the message sender cannot change them
- **No `--deliver`**, no daemon, no retry loop, no web UI, no MCP server
- **Bounded input**: UTF-8 message, 8 KiB maximum
- **Owner-only output**: `result.json` is written once with mode `0600` inside a `0700` run directory
- **Cleanup on exit**: listener, token file, mailbox, temporary files, and bridge process group are removed

## Supported Versions

This is an unofficial developer preview. Only the current `main` branch receives fixes; there are no released or packaged versions.
