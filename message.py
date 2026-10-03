#!/usr/bin/env python3
"""Send one explicitly authorized message to a configured OpenClaw agent."""

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import runtime
from runtime import SignalInterruption, run_once, signal_guard

ROOT = Path(__file__).resolve().parent
MAX_MESSAGE_BYTES = 8 * 1024
AGENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def read_message(source):
    try:
        if source == "-":
            data = sys.stdin.buffer.read(MAX_MESSAGE_BYTES + 1)
        else:
            with Path(source).open("rb") as stream:
                data = stream.read(MAX_MESSAGE_BYTES + 1)
    except OSError:
        raise ValueError("cannot read message input") from None
    if len(data) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds 8 KiB")
    try:
        message = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("message must be valid UTF-8") from None
    if not message or any(
        (ord(ch) < 32 and ch not in "\t\n\r") or ord(ch) == 127 for ch in message
    ):
        raise ValueError("message is empty or contains unsupported control characters")
    return message


def _reject_unsafe_existing_directory(directory):
    # Only the final --output-dir itself is judged here: pre-existing parent
    # directories are deliberately left untouched (mode and ownership alike),
    # per the documented "existing operator-owned parent directory modes are
    # not changed" guarantee. A pre-existing final directory is different
    # because it is about to receive a private result file, so it must
    # already be owner-controlled and owner-only before we trust it.
    info = os.stat(directory)
    if info.st_uid != os.getuid():
        raise ValueError("--output-dir already exists and is not owned by the current user")
    if info.st_mode & 0o077:
        raise ValueError("--output-dir already exists with group- or world-accessible permissions")


def _create_private_directory_tree(root):
    # Reject a symlinked final component outright rather than silently
    # following it: accepting one would let anything that can write in the
    # parent directory redirect --output-dir to a location this function
    # does not fully control, defeating the ownership/permission checks
    # below and the "every new directory is 0700" guarantee entirely.
    if root.is_symlink():
        raise ValueError("--output-dir must not be a symlink")

    # Resolve symlinks in whatever prefix of the path already exists before
    # walking it, so a symlinked *intermediate* component is followed to its
    # real location once (matching normal path resolution) instead of being
    # re-interpreted differently at each step. os.path.realpath resolves as
    # much of the path as exists and leaves any not-yet-created suffix as-is.
    resolved = Path(os.path.realpath(root))

    # Path.mkdir(parents=True) only ever applies its requested `mode` to the
    # final leaf component; every missing intermediate directory is created
    # with the platform default mode, fully subject to the process umask
    # (this is documented pathlib behavior, not a umask edge case). To make
    # every newly created directory owner-only regardless of umask, create
    # each missing component individually with an explicit os.mkdir(path,
    # 0o700) call. Since 0o700 sets no group/other bits, no umask can strip
    # anything from it, so this is safe even under a permissive umask.
    current = Path(resolved.anchor)
    parts = resolved.parts[1:]
    for index, name in enumerate(parts):
        current = current / name
        is_leaf = index == len(parts) - 1
        try:
            os.mkdir(current, 0o700)
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise ValueError(
                    f"--output-dir refers to a path where {str(current)!r} exists "
                    "and is not a directory"
                ) from None
            if is_leaf:
                _reject_unsafe_existing_directory(current)
    return resolved


def prepare_output_directory(path):
    run_directory = None
    try:
        requested = Path(path).expanduser()
        root = requested if requested.is_absolute() else Path.cwd() / requested
        resolved_root = _create_private_directory_tree(root)
        run_directory = Path(tempfile.mkdtemp(prefix="grokbot2claw-", dir=str(resolved_root)))
        run_directory.chmod(0o700)
        return run_directory.resolve()
    except BaseException as error:
        if run_directory:
            cleanup_run_directory(run_directory)
        if isinstance(error, ValueError):
            raise
        if not isinstance(error, OSError):
            raise
        raise ValueError("cannot prepare output directory") from None


def save_result(run_directory, output):
    data = (json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    path = run_directory / "result.json"
    descriptor, temporary = tempfile.mkstemp(prefix=".result-", dir=run_directory)
    try:
        os.fchmod(descriptor, 0o600)
        stream = os.fdopen(descriptor, "wb")
        descriptor = None
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        return path, hashlib.sha256(data).hexdigest()
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with contextlib.suppress(OSError):
            os.unlink(temporary)


def cleanup_run_directory(run_directory):
    if not run_directory:
        return
    with contextlib.suppress(OSError):
        for child in run_directory.iterdir():
            child.unlink()
        run_directory.rmdir()


def _check_python_version():
    # Doctor intentionally probes interpreters older than the package's target
    # version; the check below is runtime behavior, not a dead version block.
    if sys.version_info >= (3, 9):  # noqa: UP036
        return True, f"python {sys.version_info.major}.{sys.version_info.minor} >= 3.9"
    return False, (
        f"python {sys.version_info.major}.{sys.version_info.minor} is too old "
        "(fix: install Python 3.9 or newer and re-run with that interpreter)"
    )


def _check_openclaw_on_path():
    found = shutil.which("openclaw")
    if found:
        return True, f"openclaw found at {found}"
    return False, (
        "openclaw executable not found on PATH "
        "(fix: install OpenClaw per its official documentation, then ensure `openclaw` "
        "is on PATH; verify with `command -v openclaw`)"
    )


def _check_bash_on_path():
    found = shutil.which("bash")
    if found:
        return True, f"bash found at {found}"
    return False, "bash executable not found on PATH (fix: install bash; bridge.sh requires it)"


def _check_sqlite3_on_path():
    found = shutil.which("sqlite3")
    if found:
        return True, f"sqlite3 found at {found}"
    return False, (
        "sqlite3 executable not found on PATH "
        "(fix: install the SQLite command-line shell, version 3.35 or newer; "
        "bridge.sh uses it to claim messages)"
    )


def _check_executable_file(path, label):
    resolved = Path(path)
    if not resolved.is_file():
        return False, f"{label} is missing (fix: restore {resolved} from the repository)"
    if not os.access(resolved, os.X_OK):
        return False, f"{label} is not executable (fix: run `chmod +x {resolved}`)"
    return True, f"{label} exists and is executable"


def _check_adapter_script():
    return _check_executable_file(ROOT / "adapters" / "openclaw.sh", "adapters/openclaw.sh")


def _check_bridge_script():
    return _check_executable_file(ROOT / "bridge.sh", "bridge.sh")


def _check_module_imports(path, name):
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as error:  # noqa: BLE001 - report any import-time failure concretely
        return False, (
            f"{name} failed to import: {error} "
            f'(fix: run `python3 -c "import {name}"` to see the full traceback and repair it)'
        )
    return True, f"{name} imports cleanly"


def _check_runtime_imports():
    return _check_module_imports(ROOT / "runtime.py", "runtime")


def _check_http_server_imports():
    return _check_module_imports(ROOT / "http_server.py", "http_server")


def _check_temp_directory():
    try:
        with tempfile.TemporaryDirectory(prefix="grokbot2claw-doctor-") as directory:
            probe = Path(directory) / "probe"
            probe.write_text("ok", encoding="utf-8")
            if probe.read_text(encoding="utf-8") != "ok":
                raise OSError("temp file readback mismatch")
        return True, "temporary directory can be created and written"
    except OSError as error:
        return False, (
            f"cannot create or write a temporary directory: {error} "
            f"(fix: ensure {tempfile.gettempdir()} exists and is writable by your user)"
        )


def _check_session_lock_directory():
    try:
        directory = runtime._secure_lock_directory()
        return True, f"session lock directory {directory} is safe"
    except (OSError, RuntimeError) as error:
        return False, (
            f"session lock directory is unsafe or unavailable: {error} "
            "(fix: remove or fix ownership/permissions of the grokbot2claw-locks-<uid> "
            "directory under $XDG_RUNTIME_DIR or your temp directory, so it is a 0700 "
            "directory owned by you)"
        )


DOCTOR_CHECKS = [
    ("python version", _check_python_version),
    ("openclaw on PATH", _check_openclaw_on_path),
    ("bash on PATH", _check_bash_on_path),
    ("sqlite3 on PATH", _check_sqlite3_on_path),
    ("adapters/openclaw.sh", _check_adapter_script),
    ("bridge.sh", _check_bridge_script),
    ("runtime.py import", _check_runtime_imports),
    ("http_server.py import", _check_http_server_imports),
    ("temporary directory", _check_temp_directory),
    ("session lock directory", _check_session_lock_directory),
]


def run_doctor():
    """Run local preflight checks. Never invokes a model, touches the network, or writes
    outside temporary and lock directories."""
    all_ok = True
    for label, check in DOCTOR_CHECKS:
        try:
            ok, detail = check()
        except Exception as error:  # noqa: BLE001 - never let a check crash doctor mode
            ok, detail = False, f"check raised {error!r} (fix: investigate and re-run --doctor)"
        status = "ok" if ok else "FAIL"
        print(f"[{status}] {label}: {detail}")
        all_ok = all_ok and ok
    if all_ok:
        print("doctor: all checks passed")
        return 0
    print("doctor: one or more checks failed; see FAIL lines above for fixes", file=sys.stderr)
    return 1


def _main(argv=None, runner=run_once):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="run local environment preflight checks and exit (no model, no network)",
    )
    parser.add_argument(
        "--send", action="store_true", help="authorize exactly one OpenClaw invocation"
    )
    parser.add_argument("--agent", metavar="ID", help="operator-selected OpenClaw agent id")
    parser.add_argument(
        "--session-key",
        metavar="KEY",
        help=(
            "operator-selected live OpenClaw session key "
            "(default: $GROKBOT2CLAW_SESSION_KEY or 'grokbot2claw')"
        ),
    )
    parser.add_argument(
        "--message-file", metavar="PATH", help="read UTF-8 message from PATH, or - for stdin"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the full response as stable JSON"
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIRECTORY",
        help="save full response JSON in a private run directory and print a compact receipt",
    )
    args = parser.parse_args(argv)
    if args.doctor:
        return run_doctor()
    if not args.agent:
        parser.error("--agent ID is required")
    if not args.send:
        parser.error("refusing to invoke OpenClaw without --send")
    if args.message_file is None:
        parser.error("--send requires --message-file PATH (use - for stdin)")
    if not AGENT_RE.fullmatch(args.agent):
        parser.error("--agent must contain only letters, digits, underscores, or hyphens")
    if args.session_key is None:
        session_key = os.environ.get("GROKBOT2CLAW_SESSION_KEY")
        if session_key is None:
            session_key = "grokbot2claw"
    else:
        session_key = args.session_key
    if not AGENT_RE.fullmatch(session_key):
        parser.error("--session-key must contain only letters, digits, underscores, or hyphens")

    run_directory = None
    try:
        message = read_message(args.message_file)
        if args.output_dir:
            run_directory = prepare_output_directory(args.output_dir)
        result = runner(
            message, agent=args.agent, principal="grokbot-macshell", session_key=session_key
        )
    except ValueError as error:
        cleanup_run_directory(run_directory)
        print("message command: " + str(error), file=sys.stderr)
        return 2
    except RuntimeError as error:
        cleanup_run_directory(run_directory)
        detail = str(error) if str(error) == "openclaw executable not found" else "internal failure"
        print("message command failed: " + detail, file=sys.stderr)
        return 1
    except (OSError, TimeoutError):
        cleanup_run_directory(run_directory)
        print("message command failed: internal failure", file=sys.stderr)
        return 1
    except SignalInterruption:
        cleanup_run_directory(run_directory)
        raise
    clean = result.get("cleanup", {})
    if not result.get("passed") or not clean or not all(clean.values()):
        cleanup_run_directory(run_directory)
        code = result.get("error_code") or result.get("final_status") or "internal_failure"
        print("message command failed: " + str(code), file=sys.stderr)
        return 1

    output = {
        "status": "completed",
        "request_id": result["request_id"],
        "reply": result["actual_reply"],
    }
    if run_directory:
        try:
            path, digest = save_result(run_directory, output)
        except SignalInterruption:
            cleanup_run_directory(run_directory)
            raise
        except OSError:
            cleanup_run_directory(run_directory)
            print("message command failed: cannot save result", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "status": "completed",
                    "request_id": output["request_id"],
                    "result_path": str(path),
                    "sha256": digest,
                },
                separators=(",", ":"),
            )
        )
    elif args.json:
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    else:
        sys.stdout.write(output["reply"])
        if not output["reply"].endswith("\n"):
            sys.stdout.write("\n")
    return 0


def main(argv=None, runner=run_once):
    try:
        with signal_guard():
            return _main(argv, runner)
    except SignalInterruption as error:
        print(
            "message command: interrupted; local resources were stopped; upstream work may continue",
            file=sys.stderr,
        )
        return 128 + error.signum


if __name__ == "__main__":
    raise SystemExit(main())
