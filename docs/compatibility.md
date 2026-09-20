# Compatibility and Verification Status

This page records what has actually been run, where, and what each run does and does not prove. Dates are in 2026.

## Project status

GrokBot2Claw is an **unofficial developer preview** shared with a named audience. The GitHub repository may still require access until the owner publishes it. The code has not been packaged, deployed, or installed globally.

## Verified

| Date | What ran | Result | What it proves |
|---|---|---|---|
| Sep 14 | Fixture suite on macOS arm64 | Passed against Python 3.9.6, Bash 3.2.57, SQLite 3.51.0, and the OpenClaw 2026.9.1 CLI contract | Local HTTP, mailbox, bridge, adapter, parser, and cleanup with fixtures |
| Sep 14 | Local live handoff | `message.py --send` invoked the configured `rusty` agent through a dedicated OpenClaw session. The agent ran the repository's read-only fixture suite and returned an actual report: 35 passed, 0 failed, 0 errors, 0 skipped in 23.988 seconds. The repository remained unchanged. | One real end-to-end send and reply on one machine |
| Sep 16 | Built-in file output, local command line | The saved response matched the expected reply, its SHA-256 matched the receipt, and the file and run directory modes were `0600` and `0700`. Fixture coverage passed 40 tests in 21.634 seconds. | `--output-dir` behavior when started from a shell |
| Ongoing | GitHub Actions CI | Fixture suite and synthetic replay on Linux/Python 3.9 and macOS/Python 3.13. It invokes no agent, model, or provider and receives no repository secrets. | Fixture-level portability across two OS and Python versions |

## Not verified

- **Grok Bot driving `--output-dir`.** The September 16 file-output check started from the local command line, not the Grok Bot UI. An earlier capture, not included in this repository, proves Grok-initiated exact-file handling with the wrapper that motivated this built-in option; it does not prove that Grok Bot has run the new flag.
- **Fresh-machine setup.** Every live check so far ran on a machine that already had OpenClaw configured.
- **A live Linux OpenClaw installation.** CI runs fixtures on Linux; it does not install or invoke OpenClaw there.
- **Any provider, model, or Grok account behavior.** The bridge records that a command came through this local workflow. It does not authenticate Grok.

## Tested environment

- macOS arm64
- Python 3.9.6 (local) and 3.9 / 3.13 (CI)
- Bash 3.2.57 (local)
- SQLite 3.51.0 (local)
- OpenClaw 2026.9.1 CLI contract

Other versions may work; they have not been run.
