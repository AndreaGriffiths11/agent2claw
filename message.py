#!/usr/bin/env python3
"""Send one explicitly authorized message to a configured OpenClaw agent."""

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from runtime import SignalInterruption, run_once, signal_guard

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
    if not message or "\x00" in message or "\x1f" in message:
        raise ValueError("message is empty or contains unsupported control characters")
    return message


def prepare_output_directory(path):
    run_directory = None
    try:
        root = Path(path).expanduser()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not root.is_dir():
            raise OSError
        run_directory = Path(tempfile.mkdtemp(prefix="grokbot2claw-", dir=str(root)))
        run_directory.chmod(0o700)
        return run_directory.resolve()
    except BaseException as error:
        if run_directory:
            cleanup_run_directory(run_directory)
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


def _main(argv=None, runner=run_once):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--send", action="store_true", help="authorize exactly one OpenClaw invocation"
    )
    parser.add_argument(
        "--agent", required=True, metavar="ID", help="operator-selected OpenClaw agent id"
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
    if not args.send:
        parser.error("refusing to invoke OpenClaw without --send")
    if args.message_file is None:
        parser.error("--send requires --message-file PATH (use - for stdin)")
    if not AGENT_RE.fullmatch(args.agent):
        parser.error("--agent must contain only letters, digits, underscores, or hyphens")

    run_directory = None
    try:
        message = read_message(args.message_file)
        if args.output_dir:
            run_directory = prepare_output_directory(args.output_dir)
        result = runner(message, agent=args.agent, principal="grokbot-macshell")
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
