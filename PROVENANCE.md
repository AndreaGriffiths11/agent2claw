# Provenance

GrokBot2Claw was exported into a fresh Git repository with no parent history.

## Reused implementation

Source snapshot: private repository `AndreaGriffiths11/agmsg-bridge`, commit
`3e38113360c114d09fdd3a699e33ff4da4f90ee0`.

The following files were copied and then adapted for the focused one-message
Grok Bot → OpenClaw workflow:

- `message.py`
- `smoke_live.py` → `runtime.py`
- `http_server.py`
- `bridge.sh`
- `adapters/openclaw.sh`
- `test_message.py`
- `test_http_server.py`
- `test_openclaw_adapter.py`
- `.gitignore`

The source repository's MIT license is preserved verbatim at
`THIRD_PARTY_LICENSES/agmsg-bridge-MIT.txt`. Its available Git history records
work authored by Andrea Griffiths and Zo, plus a contribution co-authored by
GitHub Copilot. This record does not infer copyright ownership from Git commit
authorship.

## Upstream compatibility

The ephemeral mailbox schema and bridge behavior are compatible with
[`fujibee/agmsg`](https://github.com/fujibee/agmsg), which is MIT-licensed.
No upstream repository history or installation is copied into this repository.
Its license is preserved at `THIRD_PARTY_LICENSES/agmsg-MIT.txt`.

## Project license

The root `LICENSE` is the complete Apache License 2.0 text obtained from
<https://www.apache.org/licenses/LICENSE-2.0.txt> on September 14, 2026.
New project material and modifications are offered under Apache-2.0. The MIT
terms continue to apply to the reused portions identified above.
