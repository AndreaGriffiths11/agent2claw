# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub. Open the repository's **Security** tab and use **Report a vulnerability** (private vulnerability reporting), or draft a security advisory at:

https://github.com/AndreaGriffiths11/grokbot2claw/security/advisories/new

If that page is unavailable because private reporting has not been enabled, contact the repository owner, [@AndreaGriffiths11](https://github.com/AndreaGriffiths11), through GitHub and ask for a private channel before sharing details.

Please do not file a public issue, discussion, or pull request for an unfixed security problem.

Include what you can: the affected file and version or commit, steps to reproduce, and the impact you observed. Do not include credentials, tokens, or private prompt-derived content from `results/` in the report.

## Scope

GrokBot2Claw is a local bridge between Grok Bot Mac Shell and an OpenClaw agent. The following are in scope:

- `message.py`, `runtime.py`, `http_server.py`, `bridge.sh`, and `adapters/`;
- the loopback HTTP listener, bearer token handling, temporary SQLite mailbox, and cleanup path;
- the `--output-dir` file output mode, including file and directory permissions;
- the GitHub Actions workflow in `.github/workflows/`.

The following are out of scope for this project, although reports are still welcome as context:

- the permissions and behavior of the OpenClaw agent you select; the agent keeps its normal permissions and this bridge does not create a tool sandbox;
- Grok Bot, Mac Shell approval prompts, OpenClaw, or provider services themselves;
- prompt-injection outcomes that require the operator to approve a command they did not review.

## Supported versions

This is an unofficial developer preview. Only the current `main` branch receives fixes; there are no released or packaged versions.
