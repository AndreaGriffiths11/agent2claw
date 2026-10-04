# Operating the Bridge from Muse (or Any Agent with SSH)

Grok Bot was the first operator of this bridge, but nothing in the bridge
itself is Grok-specific. `message.py` does not know or care who invokes it:
any operator that can run a shell command on the Mac where OpenClaw runs can
send through it. This page covers the path proven with Muse, an AI agent
running on a separate machine, reaching a Mac over SSH.

The security model does not change: the bridge is loopback-only by design,
each send is one explicit operator action, and the prompt you send is a task
boundary, not a tool sandbox. Read [Setup](setup.md) and
[Responsible Use](responsible-use.md) first; everything there still applies.

## What has to be true

1. The Mac from [Setup](setup.md): OpenClaw installed, its Gateway running,
   Agent2Claw cloned, `python3 message.py --doctor` passing.
2. The operator machine can open an SSH session to that Mac as your user.
   On a home network the simplest path is Tailscale: both machines on the
   same tailnet, SSH to the Mac's tailnet address. On a LAN, the Mac's local
   address works the same way.

## 1. Create a dedicated key

On the operator machine, generate a keypair used only for this bridge, so it
can be revoked independently later:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/mac_bridge -N "" -C "bridge-operator"
```

Never reuse a personal key. The private half stays on the operator machine.

## 2. Enable Remote Login and install the key

On the Mac, turn on Remote Login (System Settings → General → Sharing →
Remote Login), then add the public key:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
cat >> ~/.ssh/authorized_keys <<'KEY'
<paste the contents of mac_bridge.pub here>
KEY
chmod 600 ~/.ssh/authorized_keys
```

Confirm with `ssh -i ~/.ssh/mac_bridge <user>@<mac-address> "echo ok"`.
Use `BatchMode=yes` for non-interactive operators so a missing key fails
fast instead of prompting for a password.

## 3. Run the bridge like any other operator

From here the flow is identical to Grok Bot's. Clone (or reuse the existing
checkout), verify, and send:

```bash
cd ~/repos/agent2claw
python3 message.py --doctor
python3 -m unittest test_message.py test_http_server.py test_openclaw_adapter.py test_bridge_privacy.py
python3 runtime.py --replay --agent <agent>
```

The replay uses fixtures and invokes no model. When it passes, a first live
send looks like this:

```bash
python3 message.py --send --agent <agent> --message-file - <<'PROMPT'
Reply with exactly this text and nothing else: bridge-ok

Do not edit files, install dependencies, access credentials, use the network,
send messages, or recursively invoke this bridge.
PROMPT
```

A `bridge-ok` reply with exit `0` proves the whole path: operator → SSH →
Mac → bridge → OpenClaw agent → reply.

## 4. Security notes

- SSH access to the Mac is shell access as your user. That is the real trust
  boundary here, larger than the bridge itself. Only grant it to operators
  you would hand your terminal to.
- Keep the key dedicated to this purpose. Revoke it by deleting its line
  from `~/.ssh/authorized_keys` on the Mac; nothing else changes.
- Prefer the least-privileged OpenClaw agent you have for bridge work, and
  keep prompts bounded the way [Setup](setup.md) describes: name the exact
  task, forbid everything else.
- If the operator platform cannot hold an SSH key or reach the Mac, this
  path does not apply. The bridge intentionally has no remote ingress of its
  own; do not expose its loopback HTTP server to the network to work around
  that.
