# Compatibility and Verification Status

This page records what has actually been run, where, and what each run does and does not prove. Dates are in 2026.

## Project status

Agent2Claw is an **unofficial developer preview**. The code has not been packaged, deployed, or installed globally.

## Verified

| Date | What ran | Result | What it proves |
|---|---|---|---|
| Sep 14 | Fixture suite on macOS arm64 | Passed against Python 3.9.6, Bash 3.2.57, SQLite 3.51.0, and the OpenClaw 2026.9.1 CLI contract | Local HTTP, mailbox, bridge, adapter, parser, and cleanup with fixtures |
| Sep 14 | Local live handoff | `message.py --send` invoked the configured `rusty` agent through a dedicated OpenClaw session. The agent ran the repository's read-only fixture suite and returned an actual report: 35 passed, 0 failed, 0 errors, 0 skipped in 23.988 seconds. The repository remained unchanged. | One real end-to-end send and reply on one machine |
| Sep 16 | Built-in file output, local command line | The saved response matched the expected reply, its SHA-256 matched the receipt, and the file and run directory modes were `0600` and `0700`. Fixture coverage passed 40 tests in 21.634 seconds. | `--output-dir` behavior when started from a shell |
| Sep 21 | Grok Bot-approved built-in file output on macOS | Andrea reported approving a Grok Bot Mac Shell invocation with `--output-dir ./results` and granting Grok Bot read and hash access to the saved file. The resulting ignored `results/grokbot2claw-0lt9b4sd/result.json` was independently checked locally: its status was `completed`, its reply was exactly `GROK2CLAW_FILE_OUTPUT_OK`, and its SHA-256 was `4a9fdd1fb45595cabd9b1978d74ffd641927fd679ad338fd17d14329d4679881`. | Grok Bot use of the built-in file-output flag, based on the approval report and independent artifact validation; the Grok Bot UI itself was not independently observed |
| Sep 21 | Local synthetic correctness/privacy regression suite | Offline fixtures cover catchable-signal cleanup, exact trailing-newline transport, invalid reply bytes, pre-parse HTTP deadlines and recovery, body-free diagnostics, atomic write-once output, and same-agent cross-process locking with different-agent independence. | Local behavior with fixtures only; no real agent, model, provider, or remote cancellation behavior |
| Oct 3 | Muse operator over SSH | A remote Muse agent reached the Mac over Tailscale SSH, ran the doctor, 70 unit tests, and fixture replay (0 real invocations), then sent `message.py --send` to the `main` OpenClaw agent with a bounded prompt. The agent replied exactly `bridge-ok`, exit 0. | End-to-end send and reply from a non-Grok operator; the bridge is sender-agnostic |
| Ongoing | GitHub Actions CI | Fixture suite and synthetic replay on Linux/Python 3.9 and macOS/Python 3.13. It invokes no agent, model, or provider and receives no repository secrets. | Fixture-level portability across two OS and Python versions |

## Not verified

- **Grok Bot UI observation.** The September 21 run was reported by Andrea and its saved artifact was independently validated locally, but the Grok Bot UI itself was not independently observed.
- **Fresh-machine setup.** Every live check so far ran on a machine that already had OpenClaw configured.
- **A live Linux OpenClaw installation.** CI runs fixtures on Linux; it does not install or invoke OpenClaw there.
- **Any provider, model, or Grok account behavior.** The bridge records that a command came through this local workflow. It does not authenticate Grok.

## Tested environment

- macOS arm64
- Python 3.9.6 (local) and 3.9 / 3.13 (CI)
- Bash 3.2.57 (local)
- SQLite 3.51.0 (local); 3.35 is the minimum, because `bridge.sh` claims rows with `UPDATE … RETURNING`
- OpenClaw 2026.9.1 CLI contract

Other versions may work; they have not been run.
