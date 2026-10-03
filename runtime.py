#!/usr/bin/env python3
"""Run one message through an ephemeral local bridge, or replay it with fixtures."""

import argparse
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import http_server

ROOT = Path(__file__).resolve().parent
SCHEMA = """
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  team TEXT NOT NULL,
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  read_at TEXT
);
CREATE INDEX idx_unread ON messages(team, to_agent, read_at) WHERE read_at IS NULL;
"""
TERMINAL = {"completed", "failed"}
LIVE_DEADLINE_SECONDS = 300
REPLAY_DEADLINE_SECONDS = 10
AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SESSION_KEY = "grokbot2claw"
REPLAY_SESSION_KEY = "grokbot2claw-replay"


class SignalInterruption(BaseException):
    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


class SessionBusyError(RuntimeError):
    pass


@contextlib.contextmanager
def signal_guard():
    """Turn catchable termination signals into cleanup-safe exceptions."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    handled = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous = {signum: signal.getsignal(signum) for signum in handled}

    def interrupt(signum, _frame):
        raise SignalInterruption(signum)

    for signum in handled:
        signal.signal(signum, interrupt)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _secure_lock_directory():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        parent = Path(runtime_dir)
        try:
            info = os.stat(parent, follow_symlinks=False)
        except OSError:
            parent = None
        else:
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                parent = None
    else:
        parent = None
    if parent is None:
        parent = Path(tempfile.gettempdir())
    path = parent / f"grokbot2claw-locks-{os.getuid()}"
    with contextlib.suppress(FileExistsError):
        path.mkdir(mode=0o700)
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError("unsafe local session lock directory")
    return path


def acquire_session_lock(agent, session_key, replay_root=None):
    """Hold one stable lock inode per local agent/session; never unlink it."""
    directory = replay_root / "fixture-locks" if replay_root else _secure_lock_directory()
    directory.mkdir(mode=0o700, exist_ok=True)
    name = hashlib.sha256(f"{agent}\0{session_key}".encode()).hexdigest() + ".lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(directory / name, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError("unsafe local session lock file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SessionBusyError(
                f"another local command is already using agent {agent!r} session {session_key!r}"
            ) from None
        metadata = json.dumps(
            {"agent": agent, "session_key": session_key, "pid": os.getpid()}, separators=(",", ":")
        ).encode()
        os.ftruncate(descriptor, 0)
        os.write(descriptor, metadata + b"\n")
        os.fsync(descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def release_session_lock(descriptor):
    if descriptor is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def request(port, token, method, path, value=None):
    body = json.dumps(value).encode("utf-8") if value is not None else None
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        return response.status, json.loads(data)
    finally:
        connection.close()


def stop_process_group(process):
    if process is None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        return False
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # Some hosted macOS runners deny the post-wait signal-0 probe. TERM was
        # accepted for this controlled group and the direct child was reaped.
        return process.poll() is not None
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return process.poll() is not None
        time.sleep(0.05)
    return False


def write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)


def run_once(
    prompt,
    *,
    agent,
    replay=False,
    expected_reply=None,
    real_openclaw=None,
    principal="grokbot-macshell",
    session_key=None,
):
    """Run one message through ephemeral HTTP, mailbox, bridge, and adapter resources."""
    if not isinstance(agent, str) or not AGENT_RE.fullmatch(agent):
        raise ValueError("invalid OpenClaw agent id")
    if session_key is not None and (
        not isinstance(session_key, str) or not AGENT_RE.fullmatch(session_key)
    ):
        raise ValueError("session_key must contain only letters, digits, underscores, or hyphens")
    if not replay and real_openclaw is None:
        real_openclaw = shutil.which("openclaw")
    if not replay and not real_openclaw:
        raise RuntimeError("openclaw executable not found")

    nonce = secrets.token_hex(12)
    expected = expected_reply
    started = time.monotonic()
    result = {
        "passed": False,
        "mode": "synthetic_replay" if replay else "live",
        "real_cli_invocations": 0,
        "nonce": nonce,
        "expected_reply": expected,
        "request_id": None,
        "http_post_status": None,
        "http_get_status": None,
        "final_status": None,
        "actual_reply": None,
        "cli_invocations": 0,
        "model_run_proven": False,
        "cli_proof": None,
        "cleanup": {},
    }
    server = thread = bridge = None
    lock_descriptor = None
    interrupted = None
    port = None
    root_path = None

    try:
        root = Path(
            tempfile.mkdtemp(prefix="grokbot2claw-replay-" if replay else "grokbot2claw-live-")
        )
        root_path = root
        session_key = REPLAY_SESSION_KEY if replay else (session_key or SESSION_KEY)
        lock_descriptor = acquire_session_lock(
            agent, session_key, replay_root=root if replay else None
        )
        db_path = root / "messages.db"
        if root_path:
            auth_path = root / "auth.json"
            adapters = root / "adapters"
            state = root / "state"
            bin_dir = root / "bin"
            proof_path = root / "cli-proof.json"
            marker_path = root / "model-invoked"
            for directory in (adapters, state, bin_dir):
                directory.mkdir()

            if replay:
                # Gateway serializer principal-CeDW0csN.js:1690-1698 and
                # observed RETRY-2.md. Only the shape is replayed, not raw logs.
                fixture = root / "synthetic-cli"
                envelope = {
                    "runId": "synthetic-" + nonce,
                    "status": "ok",
                    "summary": "completed",
                    "result": {
                        "payloads": [{"text": expected, "mediaUrl": None}],
                        "meta": {
                            "durationMs": 1,
                            "agentMeta": {
                                "sessionId": "synthetic-session",
                                "model": "synthetic",
                                "provider": "fixture",
                            },
                        },
                    },
                }
                write_executable(
                    fixture,
                    """#!/usr/bin/env python3
import json, os, stat, sys
from pathlib import Path
args = sys.argv[1:]
path = args[args.index("--message-file") + 1]
assert args == ["agent", "--agent", AGENT, "--session-key", "grokbot2claw-replay",
                "--message-file", path, "--timeout", "5", "--json"]
info = os.stat(path)
assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
assert info.st_uid == os.getuid()
assert sys.stdin.read() == ""
prompt = Path(path).read_text(encoding="utf-8")
assert "untrusted external message" in prompt
""".replace("AGENT", repr(agent))
                    + "assert "
                    + repr(expected)
                    + " in prompt\n"
                    + "print("
                    + repr(json.dumps(envelope))
                    + ")\n",
                )
                real_openclaw = str(fixture)

            http_server.ensure_secure_db_file(db_path)
            db = sqlite3.connect(db_path)
            try:
                with db:
                    db.executescript(SCHEMA)
            finally:
                db.close()

            token = secrets.token_urlsafe(48)
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            auth_payload = json.dumps(
                {"principals": {principal: {"token_sha256": digest, "recipients": [agent]}}}
            ).encode("utf-8")
            auth_fd = os.open(auth_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(auth_fd, "wb") as handle:
                handle.write(auth_payload)

            (adapters / (agent + ".sh")).symlink_to(ROOT / "adapters" / "openclaw.sh")
            write_executable(
                bin_dir / "openclaw",
                """#!/usr/bin/env python3
import json, os, re, stat, subprocess, sys

def response_error(value):
    if not isinstance(value, dict):
        return None
    result = value.get("result")
    meta = result.get("meta") if isinstance(result, dict) else None
    return value["error"] if value.get("error") is not None else (meta.get("error") if isinstance(meta, dict) else None)

def safe_error_type(value):
    error = response_error(value)
    kind = error.get("type", error.get("kind")) if isinstance(error, dict) else None
    return kind if isinstance(kind, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", kind) else None

marker = os.environ["AGMSG_LIVE_RUN_MARKER"]
try:
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print("live harness refused a second model invocation", file=sys.stderr)
    raise SystemExit(70)
os.close(fd)
argv = sys.argv[1:]
proof_argv = list(argv)
prompt_info = None
if "--message-file" in argv:
    index = argv.index("--message-file") + 1
    info = os.stat(argv[index])
    prompt_info = {
        "regular_file": stat.S_ISREG(info.st_mode),
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "owned_by_current_user": info.st_uid == os.getuid(),
    }
    proof_argv[index] = "[private prompt file]"
completed = subprocess.run(
    [os.environ["AGMSG_REAL_OPENCLAW"], *sys.argv[1:]],
    input=sys.stdin.buffer.read(), stdout=subprocess.PIPE,
)
sys.stdout.buffer.write(completed.stdout)
try:
    value = json.loads(completed.stdout)
    result = value.get("result") if isinstance(value, dict) else None
    meta = result.get("meta") if isinstance(result, dict) else None
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
    proof = {
        "exit_code": completed.returncode,
        "argv": proof_argv,
        "prompt_file": prompt_info,
        "ok": value.get("ok") if isinstance(value, dict) else None,
        "status": value.get("status") if isinstance(value, dict) else None,
        "run_id": value.get("runId") if isinstance(value, dict) else None,
        "origin": value.get("origin") if isinstance(value, dict) else None,
        "error_type": safe_error_type(value),
        "duration_ms": meta.get("durationMs") if isinstance(meta, dict) else None,
        "session_id": agent_meta.get("sessionId") if isinstance(agent_meta, dict) else None,
        "model": agent_meta.get("model") if isinstance(agent_meta, dict) else None,
        "provider": agent_meta.get("provider") if isinstance(agent_meta, dict) else None,
        "tool_summary": meta.get("toolSummary") if isinstance(meta, dict) else None,
    }
    with open(os.environ["AGMSG_LIVE_PROOF"], "w", encoding="utf-8") as stream:
        json.dump(proof, stream)
except Exception:
    pass
raise SystemExit(completed.returncode)
""",
            )

            server = http_server.create_server(
                "127.0.0.1",
                0,
                str(db_path),
                "localtest",
                str(auth_path),
                read_timeout=2.0,
                db_timeout=2.0,
                max_connections=4,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]

            env = os.environ.copy()
            env.pop("AGMSG_BRIDGE_ENV", None)
            env.update(
                {
                    "AGMSG_DB": str(db_path),
                    "AGMSG_BRIDGE_TEAM": "localtest",
                    "AGMSG_BRIDGE_AGENTS": agent,
                    "AGMSG_BRIDGE_ADAPTERS": str(adapters),
                    "AGMSG_BRIDGE_STATE": str(state),
                    "AGMSG_BRIDGE_POLL": "1",
                    "AGMSG_BRIDGE_MAX_DISPATCH": "1",
                    "AGMSG_BRIDGE_MAX_BOT_HOPS": "0",
                    "AGMSG_BRIDGE_ADAPTER_TIMEOUT": "270",
                    "OPENCLAW_BIN": str(bin_dir / "openclaw"),
                    "OPENCLAW_AGENT": agent,
                    "OPENCLAW_SESSION_KEY": session_key,
                    "OPENCLAW_TIMEOUT": "5" if replay else "240",
                    "TMPDIR": str(root),
                    "AGMSG_REAL_OPENCLAW": real_openclaw,
                    "AGMSG_LIVE_RUN_MARKER": str(marker_path),
                    "AGMSG_LIVE_PROOF": str(proof_path),
                    "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
                }
            )
            bridge = subprocess.Popen(
                ["bash", str(ROOT / "bridge.sh")],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            post_status, sent = request(
                port,
                token,
                "POST",
                "/messages",
                {
                    "recipient": agent,
                    "body": prompt,
                    "idempotency_key": "live-" + nonce,
                },
            )
            result["http_post_status"] = post_status
            result["request_id"] = sent.get("id")
            if post_status != 202 or not result["request_id"]:
                raise RuntimeError("HTTP submit did not return a pending request")

            deadline = time.monotonic() + (
                REPLAY_DEADLINE_SECONDS if replay else LIVE_DEADLINE_SECONDS
            )
            while time.monotonic() < deadline:
                # The bridge's own diagnostics are discarded, so an early exit
                # (missing sqlite3, FATAL preflight) must be detected here or the
                # command would silently wait out the whole deadline.
                if bridge.poll() is not None:
                    result["error_code"] = "bridge_exited"
                    raise RuntimeError("bridge process exited before the request completed")
                get_status, current = request(
                    port, token, "GET", "/messages/" + result["request_id"]
                )
                result["http_get_status"] = get_status
                if get_status != 200:
                    raise RuntimeError("HTTP status read failed")
                if current.get("status") in TERMINAL:
                    result["final_status"] = current.get("status")
                    result["actual_reply"] = current.get("reply")
                    if current.get("error"):
                        result["error_code"] = current["error"]
                    break
                time.sleep(2)
            else:
                result["error_code"] = "deadline_exceeded"
                raise TimeoutError("request did not reach a terminal state before its deadline")

            http_server.ensure_secure_db_file(db_path)
            db = sqlite3.connect(db_path)
            try:
                with db:
                    replies = db.execute(
                        "SELECT body FROM messages WHERE team=? AND from_agent=? AND to_agent=?",
                        ("localtest", agent, principal),
                    ).fetchall()
            finally:
                db.close()
            result["mailbox_reply_exact"] = replies == [(result["actual_reply"],)]
            result["cli_invocations"] = 1 if marker_path.exists() else 0
            if proof_path.exists():
                result["cli_proof"] = json.loads(proof_path.read_text(encoding="utf-8"))
            result["real_cli_invocations"] = 0 if replay else result["cli_invocations"]
            result["cleanup"]["prompt_and_output_files_removed"] = not any(
                root.glob("grokbot2claw-openclaw*")
            )
            result["model_run_proven"] = bool(
                not replay
                and isinstance(result["cli_proof"], dict)
                and (result["cli_proof"].get("run_id") or result["cli_proof"].get("session_id"))
                and result["cli_proof"].get("model")
            )
        result["passed"] = (
            result["final_status"] == "completed"
            and (expected is None or result["actual_reply"] == expected)
            and result["cli_invocations"] == 1
            and result["mailbox_reply_exact"]
            and (replay or result["model_run_proven"])
            and isinstance(result["cli_proof"], dict)
            and result["cli_proof"].get("exit_code") == 0
        )
    except SignalInterruption as error:
        interrupted = error
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, signal.SIG_IGN)
    except SessionBusyError as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
        result["error_code"] = "session_busy"
    except Exception as error:
        result["failure"] = type(error).__name__ + ": " + str(error)
    finally:
        if server is not None:
            if thread is not None and thread.is_alive():
                server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        result["cleanup"]["bridge_process_group_gone"] = stop_process_group(bridge)
        if port is not None:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                connection.request("GET", "/")
                connection.getresponse()
                connection.close()
                result["cleanup"]["port_closed"] = False
            except OSError:
                result["cleanup"]["port_closed"] = True
        if root_path is not None:
            shutil.rmtree(root_path, ignore_errors=True)
        result["cleanup"]["temporary_directory_removed"] = bool(
            root_path and not root_path.exists()
        )
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        release_session_lock(lock_descriptor)

    if interrupted is not None:
        raise interrupted

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live", action="store_true", help="authorize exactly one real OpenClaw model invocation"
    )
    mode.add_argument(
        "--replay",
        action="store_true",
        help="synthetic Gateway-shape replay; never invoke installed OpenClaw",
    )
    parser.add_argument("--agent", required=True, help="operator-selected OpenClaw agent id")
    args = parser.parse_args()
    if not AGENT_RE.fullmatch(args.agent):
        parser.error("--agent must contain only letters, digits, underscores, or hyphens")
    if not args.live and not args.replay:
        parser.error("refusing to call a real model without --live")
    nonce = secrets.token_hex(12)
    expected = ("SYNTHETIC REPLAY ACK " if args.replay else "ACK ") + nonce
    prompt = (
        "This is an explicitly authorized connectivity test. Reply with exactly "
        + expected
        + ". Do not call tools, read files, change state, send messages, or start further tasks."
    )
    try:
        with signal_guard():
            result = run_once(
                prompt,
                agent=args.agent,
                replay=args.replay,
                expected_reply=expected,
                principal="local-smoke",
            )
    except SignalInterruption as error:
        print(
            "runtime interrupted; local resources were stopped; upstream work may continue",
            file=sys.stderr,
        )
        raise SystemExit(128 + error.signum) from None
    # Keep the historical smoke proof nonce stable at the outer command boundary.
    result["nonce"] = nonce
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["passed"] and all(result["cleanup"].values()) else 1)


if __name__ == "__main__":
    main()
