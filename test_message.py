import contextlib
import hashlib
import io
import json
import os
import signal
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import message
import runtime
from runtime import run_once


class MessageCommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def call_main(self, argv, runner):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = message.main(argv, runner=runner)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_send_requires_explicit_flag_and_input(self):
        command = Path(message.__file__)
        for argv in ([], ["--message-file", "-"]):
            result = subprocess.run(
                ["python3", str(command), *argv],
                input="hello",
                text=True,
                capture_output=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
        result = subprocess.run(
            ["python3", str(command), "--help"], text=True, capture_output=True, timeout=3
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--send", result.stdout)

    def test_input_validation_happens_before_runner(self):
        runner = mock.Mock()
        cases = [
            b"",
            b"\xff",
            b"x" * (message.MAX_MESSAGE_BYTES + 1),
            b"bad\x00body",
            b"bad\x1fbody",
        ]
        for index, data in enumerate(cases):
            with self.subTest(index=index):
                path = self.root / str(index)
                path.write_bytes(data)
                status, stdout, stderr = self.call_main(
                    ["--send", "--agent", "docs-agent", "--message-file", str(path)], runner
                )
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                self.assertIn("message command:", stderr)
        runner.assert_not_called()

    def test_stdin_and_json_output_are_stable(self):
        reply = "OpenClaw says: ¡listo!"
        result = {
            "passed": True,
            "request_id": "fixture-id",
            "actual_reply": reply,
            "cleanup": {"port_closed": True},
        }
        stdin = mock.Mock()
        stdin.buffer = io.BytesIO("line one\n'quotes' $(literal) 世界".encode())
        runner = mock.Mock(return_value=result)
        with mock.patch.object(message.sys, "stdin", stdin):
            status, stdout, stderr = self.call_main(
                ["--send", "--agent", "docs-agent", "--message-file", "-", "--json"], runner
            )
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(
            json.loads(stdout), {"status": "completed", "request_id": "fixture-id", "reply": reply}
        )
        runner.assert_called_once_with(
            "line one\n'quotes' $(literal) 世界", agent="docs-agent", principal="grokbot-macshell"
        )

    def test_output_directory_saves_exact_json_and_prints_compact_receipt(self):
        reply = 'line one\n`code` "quotes" — 世界\n\n'
        result = {
            "passed": True,
            "request_id": "fixture-id",
            "actual_reply": reply,
            "cleanup": {"port_closed": True},
        }
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        output_root = self.root / "results with spaces 世界"

        status, stdout, stderr = self.call_main(
            [
                "--send",
                "--agent",
                "docs-agent",
                "--message-file",
                str(message_path),
                "--output-dir",
                str(output_root),
                "--json",
            ],
            mock.Mock(return_value=result),
        )

        self.assertEqual((status, stderr), (0, ""))
        receipt = json.loads(stdout)
        self.assertEqual(set(receipt), {"status", "request_id", "result_path", "sha256"})
        self.assertNotIn(reply, stdout)
        result_path = Path(receipt["result_path"])
        data = result_path.read_bytes()
        self.assertEqual(
            json.loads(data), {"status": "completed", "request_id": "fixture-id", "reply": reply}
        )
        self.assertEqual(receipt["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(os.stat(result_path.parent).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(result_path).st_mode & 0o777, 0o600)

    def test_result_write_is_atomic_write_once_and_cleans_failed_temporary_file(self):
        run_directory = self.root / "run"
        run_directory.mkdir(mode=0o700)
        final = run_directory / "result.json"
        final.write_text("operator artifact")
        with self.assertRaises(FileExistsError):
            message.save_result(run_directory, {"reply": "private reply"})
        self.assertEqual(final.read_text(), "operator artifact")
        self.assertEqual(list(run_directory.iterdir()), [final])

        final.unlink()
        with mock.patch.object(message.os, "link", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                message.save_result(run_directory, {"reply": "private reply"})
        self.assertEqual(list(run_directory.iterdir()), [])

    def test_signal_during_result_write_removes_run_directory(self):
        message_path = self.root / "message.txt"
        message_path.write_text("private prompt")
        output_root = self.root / "results"
        result = {
            "passed": True,
            "request_id": "fixture-id",
            "actual_reply": "private reply",
            "cleanup": {"port_closed": True},
        }
        with mock.patch.object(
            message.os, "fsync", side_effect=runtime.SignalInterruption(signal.SIGTERM)
        ):
            status, stdout, stderr = self.call_main(
                [
                    "--send",
                    "--agent",
                    "docs-agent",
                    "--message-file",
                    str(message_path),
                    "--output-dir",
                    str(output_root),
                ],
                mock.Mock(return_value=result),
            )
        self.assertEqual((status, stdout), (143, ""))
        self.assertIn("upstream work may continue", stderr)
        self.assertEqual(list(output_root.iterdir()), [])

    def test_sighup_returns_conventional_status_without_private_detail(self):
        message_path = self.root / "message.txt"
        message_path.write_text("PROMPT_PRIVATE_HUP")
        status, stdout, stderr = self.call_main(
            ["--send", "--agent", "docs-agent", "--message-file", str(message_path)],
            mock.Mock(side_effect=runtime.SignalInterruption(signal.SIGHUP)),
        )
        self.assertEqual((status, stdout), (129, ""))
        self.assertIn("upstream work may continue", stderr)
        self.assertNotIn("PROMPT_PRIVATE_HUP", stderr)

    def test_invalid_output_directory_is_rejected_before_runner(self):
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        invalid = self.root / "not-a-directory"
        invalid.write_text("existing user file")
        runner = mock.Mock()

        status, stdout, stderr = self.call_main(
            [
                "--send",
                "--agent",
                "docs-agent",
                "--message-file",
                str(message_path),
                "--output-dir",
                str(invalid),
            ],
            runner,
        )

        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "message command: cannot prepare output directory\n")
        self.assertEqual(invalid.read_text(), "existing user file")
        runner.assert_not_called()

    def test_output_directory_is_removed_when_bridge_fails(self):
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        output_root = self.root / "results"
        runner = mock.Mock(
            return_value={
                "passed": False,
                "error_code": "adapter_failed",
                "cleanup": {"port_closed": True},
            }
        )

        status, stdout, stderr = self.call_main(
            [
                "--send",
                "--agent",
                "docs-agent",
                "--message-file",
                str(message_path),
                "--output-dir",
                str(output_root),
            ],
            runner,
        )

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "message command failed: adapter_failed\n")
        self.assertEqual(list(output_root.iterdir()), [])

    def test_output_directory_is_removed_when_runner_raises(self):
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        output_root = self.root / "results"

        status, stdout, stderr = self.call_main(
            [
                "--send",
                "--agent",
                "docs-agent",
                "--message-file",
                str(message_path),
                "--output-dir",
                str(output_root),
            ],
            mock.Mock(side_effect=TimeoutError("private detail")),
        )

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "message command failed: internal failure\n")
        self.assertEqual(list(output_root.iterdir()), [])

    def test_failure_has_no_stdout_or_internal_detail(self):
        path = self.root / "message.txt"
        path.write_text("safe request")
        for runner, expected in (
            (
                mock.Mock(
                    return_value={
                        "passed": False,
                        "final_status": "failed",
                        "error_code": "adapter_failed",
                        "failure": "token=private /private/path",
                        "cleanup": {"port_closed": True},
                    }
                ),
                "message command failed: adapter_failed\n",
            ),
            (
                mock.Mock(side_effect=TimeoutError("token=private /private/path")),
                "message command failed: internal failure\n",
            ),
        ):
            with self.subTest(expected=expected):
                status, stdout, stderr = self.call_main(
                    ["--send", "--agent", "docs-agent", "--message-file", str(path)], runner
                )
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, expected)

    def test_real_http_bridge_adapter_fixture_preserves_body_and_invokes_once(self):
        capture = self.root / "capture.json"
        counter = self.root / "count"
        reply = "fixture custom reply — not ACK"
        fake = self.root / "openclaw"
        fake.write_text(
            textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, os, stat, sys
            from pathlib import Path
            args = sys.argv[1:]
            prompt_path = args[args.index("--message-file") + 1]
            info = os.stat(prompt_path)
            assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            assert info.st_uid == os.getuid() and sys.stdin.read() == ""
            Path({str(counter)!r}).write_text("1")
            Path({str(capture)!r}).write_text(json.dumps({{"args": args, "prompt": Path(prompt_path).read_text()}}))
            print(json.dumps({{"runId": "fixture-run", "status": "ok", "summary": "completed",
                "result": {{"payloads": [{{"text": {reply!r}, "mediaUrl": None}}],
                "meta": {{"agentMeta": {{"sessionId": "fixture-session", "model": "fixture-model",
                "provider": "fixture-provider"}}}}}}}}))
        """)
        )
        fake.chmod(0o700)
        body = "First line\n¡Hola, 世界! 'quotes' $(touch never) `false`; & |"
        result = run_once(
            body, agent="separate-agent", real_openclaw=str(fake), expected_reply=reply
        )
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["actual_reply"], reply)
        self.assertEqual(counter.read_text(), "1")
        captured = json.loads(capture.read_text())
        self.assertTrue(captured["prompt"].endswith(body))
        self.assertNotIn(body, captured["args"])
        self.assertNotIn("--deliver", captured["args"])
        self.assertEqual(captured["args"][2], "separate-agent")
        self.assertEqual(captured["args"][4], "grokbot2claw")
        self.assertTrue(all(result["cleanup"].values()))

    def test_bridge_failure_still_cleans_ephemeral_resources(self):
        counter = self.root / "failed-count"
        fake = self.root / "failing-openclaw"
        fake.write_text(
            textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json
            from pathlib import Path
            Path({str(counter)!r}).write_text("1")
            print(json.dumps({{"ok": False, "error": {{"type": "cli_error"}}}}))
            raise SystemExit(1)
        """)
        )
        fake.chmod(0o700)
        result = run_once(
            "harmless failure fixture", agent="separate-agent", real_openclaw=str(fake)
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_code"], "adapter_failed")
        self.assertEqual(counter.read_text(), "1")
        self.assertEqual(result["cli_invocations"], 1)
        self.assertTrue(all(result["cleanup"].values()))

    def test_interruption_during_setup_cleans_ephemeral_resources(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        for target in ("write_executable", "thread"):
            with self.subTest(target=target):
                patch = (
                    mock.patch.object(
                        runtime,
                        "write_executable",
                        side_effect=runtime.SignalInterruption(signal.SIGHUP),
                    )
                    if target == "write_executable"
                    else mock.patch.object(
                        runtime.threading,
                        "Thread",
                        side_effect=runtime.SignalInterruption(signal.SIGHUP),
                    )
                )
                with mock.patch.dict(os.environ, {"TMPDIR": str(scratch)}), patch:
                    with self.assertRaises(runtime.SignalInterruption) as caught:
                        with runtime.signal_guard():
                            run_once(
                                "private prompt",
                                agent="setup-agent",
                                replay=True,
                                expected_reply="private reply",
                            )
                self.assertEqual(caught.exception.signum, signal.SIGHUP)
                self.assertEqual(list(scratch.iterdir()), [])

    def test_cross_process_session_lock_and_signal_release(self):
        runtime_dir = self.root / "runtime"
        scratch = self.root / "scratch"
        output_root = self.root / "results"
        runtime_dir.mkdir(mode=0o700)
        scratch.mkdir(mode=0o700)
        calls = self.root / "calls"
        started = self.root / "started"
        child_pid = self.root / "child-pid"
        fake = self.root / "locking-openclaw"
        fake.write_text(
            textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json, os, sys, time
            from pathlib import Path
            args = sys.argv[1:]
            agent = args[args.index("--agent") + 1]
            with Path({str(calls)!r}).open("a") as stream:
                stream.write(agent + "\\n")
            Path({str(started)!r}).write_text(agent)
            delay = float(os.environ.get("FIXTURE_SLEEP", "0"))
            if delay:
                Path({str(child_pid)!r}).write_text(str(os.getpid()))
            time.sleep(delay)
            print(json.dumps({{"runId": "fixture-run", "status": "ok", "result": {{
                "payloads": [{{"text": "fixture reply", "mediaUrl": None}}],
                "meta": {{"agentMeta": {{"sessionId": "fixture", "model": "fixture",
                "provider": "fixture"}}}}
            }}}}))
            """
            )
        )
        fake.chmod(0o700)
        message_path = self.root / "message.txt"
        message_path.write_text("PROMPT_PRIVATE_LOCK")
        env = os.environ.copy()
        env.update(
            {
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "TMPDIR": str(scratch),
                "FIXTURE_SLEEP": "30",
                "PATH": str(self.root) + os.pathsep + env["PATH"],
            }
        )
        # message.py normally discovers openclaw; provide the fixture under that fixed name.
        (self.root / "openclaw").symlink_to(fake)
        first = subprocess.Popen(
            [
                "python3",
                str(Path(message.__file__)),
                "--send",
                "--agent",
                "same-agent",
                "--message-file",
                str(message_path),
                "--output-dir",
                str(output_root),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 8
        while time.time() < deadline and not started.exists():
            time.sleep(0.05)
        self.assertTrue(started.exists(), "fixture CLI did not start")

        child_code = (
            "import json,sys; from runtime import run_once; "
            "print(json.dumps(run_once('PROMPT_PRIVATE_LOCK', agent=sys.argv[1], "
            "real_openclaw=sys.argv[2], expected_reply='fixture reply')))"
        )
        second_env = dict(env, FIXTURE_SLEEP="0")
        same = subprocess.run(
            ["python3", "-c", child_code, "same-agent", str(fake)],
            cwd=Path(message.__file__).parent,
            env=second_env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        same_result = json.loads(same.stdout)
        self.assertEqual(same_result["error_code"], "session_busy")
        self.assertEqual(same_result["cli_invocations"], 0)
        different = subprocess.run(
            ["python3", "-c", child_code, "different-agent", str(fake)],
            cwd=Path(message.__file__).parent,
            env=second_env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertTrue(json.loads(different.stdout)["passed"], different.stderr)

        first.send_signal(signal.SIGTERM)
        stdout, stderr = first.communicate(timeout=15)
        self.assertEqual((first.returncode, stdout), (143, ""))
        self.assertIn("upstream work may continue", stderr)
        self.assertNotIn("PROMPT_PRIVATE_LOCK", stderr)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(child_pid.read_text()), 0)
        self.assertEqual(list(output_root.iterdir()), [])
        self.assertEqual(list(scratch.glob("grokbot2claw-live-*")), [])

        subsequent = subprocess.run(
            ["python3", "-c", child_code, "same-agent", str(fake)],
            cwd=Path(message.__file__).parent,
            env=second_env,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertTrue(json.loads(subsequent.stdout)["passed"], subsequent.stderr)
        self.assertEqual(calls.read_text().splitlines().count("same-agent"), 2)
        self.assertIn("different-agent", calls.read_text().splitlines())

        lock_directory = runtime_dir / f"grokbot2claw-locks-{os.getuid()}"
        self.assertEqual(os.stat(lock_directory).st_mode & 0o777, 0o700)
        for lock in lock_directory.iterdir():
            self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)
            metadata = lock.read_text()
            self.assertNotIn("PROMPT_PRIVATE_LOCK", metadata)
            self.assertIn('"session_key":"grokbot2claw"', metadata)


if __name__ == "__main__":
    unittest.main(verbosity=2)
