import contextlib
import fcntl
import hashlib
import io
import json
import os
import shutil
import signal
import stat
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
            "line one\n'quotes' $(literal) 世界",
            agent="docs-agent",
            principal="grokbot-macshell",
            session_key="grokbot2claw",
        )

    def _make_send_result(self):
        return {
            "passed": True,
            "request_id": "fixture-id",
            "actual_reply": "ok",
            "cleanup": {"port_closed": True},
        }

    def test_session_key_flag_reaches_runner(self):
        message_path = self.root / "message.txt"
        message_path.write_text("hello", encoding="utf-8")
        runner = mock.Mock(return_value=self._make_send_result())
        status, _, stderr = self.call_main(
            [
                "--send",
                "--agent",
                "docs-agent",
                "--session-key",
                "custom1",
                "--message-file",
                str(message_path),
            ],
            runner,
        )
        self.assertEqual((status, stderr), (0, ""))
        runner.assert_called_once_with(
            "hello", agent="docs-agent", principal="grokbot-macshell", session_key="custom1"
        )

    def test_session_key_env_var_reaches_runner(self):
        message_path = self.root / "message.txt"
        message_path.write_text("hello", encoding="utf-8")
        runner = mock.Mock(return_value=self._make_send_result())
        with mock.patch.dict(os.environ, {"GROKBOT2CLAW_SESSION_KEY": "fromenv"}, clear=False):
            status, _, stderr = self.call_main(
                ["--send", "--agent", "docs-agent", "--message-file", str(message_path)],
                runner,
            )
        self.assertEqual((status, stderr), (0, ""))
        runner.assert_called_once_with(
            "hello", agent="docs-agent", principal="grokbot-macshell", session_key="fromenv"
        )

    def test_session_key_flag_beats_env_var(self):
        message_path = self.root / "message.txt"
        message_path.write_text("hello", encoding="utf-8")
        runner = mock.Mock(return_value=self._make_send_result())
        with mock.patch.dict(os.environ, {"GROKBOT2CLAW_SESSION_KEY": "fromenv"}, clear=False):
            status, _, stderr = self.call_main(
                [
                    "--send",
                    "--agent",
                    "docs-agent",
                    "--session-key",
                    "fromflag",
                    "--message-file",
                    str(message_path),
                ],
                runner,
            )
        self.assertEqual((status, stderr), (0, ""))
        runner.assert_called_once_with(
            "hello", agent="docs-agent", principal="grokbot-macshell", session_key="fromflag"
        )

    def test_invalid_session_key_is_rejected(self):
        message_path = self.root / "message.txt"
        message_path.write_text("hello", encoding="utf-8")
        result = subprocess.run(
            [
                "python3",
                str(Path(message.__file__)),
                "--send",
                "--agent",
                "docs-agent",
                "--session-key",
                "bad key!",
                "--message-file",
                str(message_path),
            ],
            text=True,
            capture_output=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--session-key", result.stderr)

    def test_empty_session_key_flag_is_rejected(self):
        # Explicit --session-key "" must not fall through to env/default.
        message_path = self.root / "message.txt"
        message_path.write_text("hello", encoding="utf-8")
        env = os.environ.copy()
        env["GROKBOT2CLAW_SESSION_KEY"] = "fromenv"
        result = subprocess.run(
            [
                "python3",
                str(Path(message.__file__)),
                "--send",
                "--agent",
                "docs-agent",
                "--session-key",
                "",
                "--message-file",
                str(message_path),
            ],
            text=True,
            capture_output=True,
            timeout=3,
            env=env,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--session-key", result.stderr)

    def test_run_once_rejects_invalid_session_key(self):
        with self.assertRaises(ValueError) as failure:
            run_once("hello", agent="docs-agent", replay=True, session_key="bad key!")
        self.assertIn("session_key", str(failure.exception))

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
        with (
            mock.patch.object(message.os, "link", side_effect=OSError("fixture failure")),
            self.assertRaises(OSError),
        ):
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
        self.assertEqual(
            stderr,
            "message command: --output-dir refers to a path where "
            f"{str(invalid.resolve())!r} exists and is not a directory\n",
        )
        self.assertEqual(invalid.read_text(), "existing user file")
        runner.assert_not_called()

    def test_nested_output_directory_components_are_all_owner_only_under_permissive_umask(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        output_root = self.root / "new1" / "new2" / "new3"

        run_directory = message.prepare_output_directory(output_root)

        self.assertTrue(run_directory.is_relative_to(output_root.resolve()))
        for candidate in (self.root / "new1", self.root / "new1" / "new2", output_root):
            self.assertEqual(stat.S_IMODE(os.stat(candidate).st_mode), 0o700)

    def test_preexisting_intermediate_directory_mode_is_left_unchanged(self):
        previous_umask = os.umask(0o022)
        self.addCleanup(os.umask, previous_umask)
        preexisting = self.root / "shared"
        preexisting.mkdir(mode=0o755)
        os.chmod(preexisting, 0o755)
        output_root = preexisting / "private-child"

        message.prepare_output_directory(output_root)

        self.assertEqual(stat.S_IMODE(os.stat(preexisting).st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(os.stat(output_root).st_mode), 0o700)

    def test_preexisting_final_directory_owned_by_another_user_is_rejected(self):
        output_root = self.root / "results"
        output_root.mkdir(mode=0o700)
        with (
            mock.patch.object(message.os, "getuid", return_value=os.getuid() + 1),
            self.assertRaises(ValueError) as failure,
        ):
            message.prepare_output_directory(output_root)
        self.assertIn("not owned by the current user", str(failure.exception))

    def test_preexisting_final_directory_that_is_group_or_world_writable_is_rejected(self):
        output_root = self.root / "results"
        output_root.mkdir(mode=0o755)
        os.chmod(output_root, 0o755)

        with self.assertRaises(ValueError) as failure:
            message.prepare_output_directory(output_root)

        self.assertIn("group- or world-accessible", str(failure.exception))

    def test_symlinked_final_output_directory_is_rejected(self):
        real_target = self.root / "real-target"
        real_target.mkdir(mode=0o700)
        link = self.root / "link-to-target"
        link.symlink_to(real_target, target_is_directory=True)

        with self.assertRaises(ValueError) as failure:
            message.prepare_output_directory(link)

        self.assertIn("must not be a symlink", str(failure.exception))

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

    def test_bridge_exit_fails_fast_with_safe_error_code(self):
        # A PATH with bash and python3 but no sqlite3 makes bridge.sh exit in its
        # preflight. The runtime must notice instead of waiting out the deadline.
        thin_bin = self.root / "thin-bin"
        thin_bin.mkdir()
        for name in ("bash", "python3"):
            (thin_bin / name).symlink_to(shutil.which(name))
        fake = self.root / "never-called-openclaw"
        fake.write_text("#!/usr/bin/env bash\nexit 1\n")
        fake.chmod(0o700)
        started = time.monotonic()
        with mock.patch.dict(os.environ, {"PATH": str(thin_bin)}):
            result = run_once("harmless fixture", agent="separate-agent", real_openclaw=str(fake))
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_code"], "bridge_exited")
        self.assertEqual(result["cli_invocations"], 0)
        self.assertLess(time.monotonic() - started, 60)
        self.assertTrue(all(result["cleanup"].values()), result["cleanup"])

    def test_deadline_sets_safe_error_code_and_cleans_up(self):
        fake = self.root / "slow-openclaw"
        fake.write_text("#!/usr/bin/env bash\nsleep 30\n")
        fake.chmod(0o700)
        with mock.patch.object(runtime, "LIVE_DEADLINE_SECONDS", 3):
            result = run_once("harmless fixture", agent="separate-agent", real_openclaw=str(fake))
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_code"], "deadline_exceeded")
        self.assertTrue(all(result["cleanup"].values()), result["cleanup"])

    def test_auth_and_mailbox_files_are_created_owner_only(self):
        # Reproduce the run under a permissive umask so a regression that lets
        # auth.json or messages.db inherit the umask (instead of forcing 0o600
        # at creation time) would be caught here.
        captured_modes = {}
        real_rmtree = runtime.shutil.rmtree

        def snapshot_then_rmtree(path, *args, **kwargs):
            root = Path(path)
            captured_modes["auth"] = stat.S_IMODE(os.stat(root / "auth.json").st_mode)
            captured_modes["db"] = stat.S_IMODE(os.stat(root / "messages.db").st_mode)
            return real_rmtree(path, *args, **kwargs)

        old_umask = os.umask(0o022)
        try:
            with mock.patch.object(runtime.shutil, "rmtree", side_effect=snapshot_then_rmtree):
                result = run_once(
                    "owner-only permission fixture reply",
                    agent="perm-agent",
                    replay=True,
                    expected_reply="owner-only permission fixture reply",
                )
        finally:
            os.umask(old_umask)
        self.assertTrue(result["passed"], result)
        self.assertEqual(captured_modes["auth"], 0o600)
        self.assertEqual(captured_modes["db"], 0o600)

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
                with (
                    mock.patch.dict(os.environ, {"TMPDIR": str(scratch)}),
                    patch,
                    runtime.signal_guard(),
                    self.assertRaises(runtime.SignalInterruption) as caught,
                ):
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
            started_path = Path({str(started)!r})
            with started_path.open("w", encoding="utf-8") as stream:
                stream.write(agent)
                stream.flush()
                os.fsync(stream.fileno())
            delay = float(os.environ.get("FIXTURE_SLEEP", "0"))
            if delay:
                Path({str(child_pid)!r}).write_text(str(os.getpid()))
            time.sleep(delay)
            print(json.dumps({{"runId": "fixture-run", "status": "ok", "result": {{
                "payloads": [{{"text": "fixture reply", "mediaUrl": None}}],
                "meta": {{"agentMeta": {{"sessionId": "fixture", "model": "fixture",
                "provider": "fixture"}}}}
            }}}}))
            """)
        )
        fake.chmod(0o700)
        message_path = self.root / "message.txt"
        message_path.write_text("PROMPT_PRIVATE_LOCK")
        env = os.environ.copy()
        env.update(
            {
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "TMPDIR": str(scratch),
                "FIXTURE_SLEEP": "120",
                "PATH": str(self.root) + os.pathsep + env["PATH"],
            }
        )
        (self.root / "openclaw").symlink_to(fake)
        first_stdout = self.root / "first-stdout"
        first_stderr = self.root / "first-stderr"
        capture_stack = contextlib.ExitStack()
        first_stdout_fd = os.open(first_stdout, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        capture_stack.callback(os.close, first_stdout_fd)
        first_stderr_fd = os.open(first_stderr, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        capture_stack.callback(os.close, first_stderr_fd)
        first = None

        def read_capture(path):
            return path.read_text(encoding="utf-8")

        def lock_is_held():
            lock_directory = runtime_dir / f"grokbot2claw-locks-{os.getuid()}"
            expected_lock = lock_directory / (
                hashlib.sha256(b"same-agent\0grokbot2claw").hexdigest() + ".lock"
            )
            if not expected_lock.exists():
                return False
            try:
                raw = expected_lock.read_text(encoding="utf-8")
            except OSError:
                return False
            try:
                metadata = json.loads(raw)
            except (TypeError, ValueError):
                return False
            if not isinstance(metadata, dict):
                return False
            if metadata.get("agent") != "same-agent":
                return False
            if metadata.get("session_key") != "grokbot2claw":
                return False
            try:
                if int(metadata.get("pid")) != first.pid:
                    return False
            except (TypeError, ValueError):
                return False
            try:
                probe_fd = os.open(expected_lock, os.O_RDWR)
            except OSError:
                return False
            try:
                try:
                    fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                except OSError:
                    return False
                else:
                    with contextlib.suppress(OSError):
                        fcntl.flock(probe_fd, fcntl.LOCK_UN)
                    return False
            finally:
                os.close(probe_fd)

        try:
            first = subprocess.Popen(
                [
                    "python3",
                    "-u",
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
                stdout=first_stdout_fd,
                stderr=first_stderr_fd,
                text=True,
                start_new_session=True,
            )
            capture_stack.close()
            phase_a_deadline = time.monotonic() + 60
            while time.monotonic() < phase_a_deadline:
                if first.poll() is not None:
                    stdout = read_capture(first_stdout)
                    stderr = read_capture(first_stderr)
                    self.fail(
                        "first process exited before session lock was held; "
                        f"code={first.returncode} lock_is_held={lock_is_held()} "
                        f"stdout={stdout!r} stderr={stderr!r}"
                    )
                if lock_is_held():
                    break
                time.sleep(0.05)
            if not lock_is_held():
                stdout = read_capture(first_stdout)
                stderr = read_capture(first_stderr)
                self.fail(
                    "fixture CLI did not hold the expected lock within Phase A deadline; "
                    f"lock_is_held={lock_is_held()} started={started.exists()} "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )

            phase_b_deadline = time.monotonic() + 60
            while time.monotonic() < phase_b_deadline:
                if first.poll() is not None:
                    stdout = read_capture(first_stdout)
                    stderr = read_capture(first_stderr)
                    self.fail(
                        "first process exited before started marker was created; "
                        f"code={first.returncode} lock_is_held={lock_is_held()} "
                        f"stdout={stdout!r} stderr={stderr!r}"
                    )
                if started.exists():
                    break
                time.sleep(0.05)
            if not started.exists():
                stdout = read_capture(first_stdout)
                stderr = read_capture(first_stderr)
                self.fail(
                    "fixture CLI did not create the started marker within Phase B deadline; "
                    f"lock_is_held={lock_is_held()} started={started.exists()} "
                    f"stdout={stdout!r} stderr={stderr!r}"
                )

            child_code = (
                "import json,sys; from runtime import run_once; "
                "print(json.dumps(run_once('PROMPT_PRIVATE_LOCK', agent=sys.argv[1], "
                "real_openclaw=sys.argv[2], expected_reply='fixture reply')))"
            )
            second_env = dict(env, FIXTURE_SLEEP="0")
            same = subprocess.run(
                ["python3", "-u", "-c", child_code, "same-agent", str(fake)],
                cwd=Path(message.__file__).parent,
                env=second_env,
                text=True,
                capture_output=True,
                timeout=90,
            )
            self.assertEqual(same.returncode, 0, same.stderr)
            same_result = json.loads(same.stdout)
            self.assertEqual(same_result["error_code"], "session_busy")
            self.assertEqual(same_result["cli_invocations"], 0)
            different = subprocess.run(
                ["python3", "-u", "-c", child_code, "different-agent", str(fake)],
                cwd=Path(message.__file__).parent,
                env=second_env,
                text=True,
                capture_output=True,
                timeout=90,
            )
            self.assertEqual(different.returncode, 0, different.stderr)
            self.assertTrue(json.loads(different.stdout)["passed"], different.stderr)

            os.killpg(first.pid, signal.SIGTERM)
            self.assertEqual(first.wait(timeout=30), 143)
            stdout = read_capture(first_stdout)
            stderr = read_capture(first_stderr)
            self.assertEqual(stdout, "")
            self.assertIn("upstream work may continue", stderr)
            self.assertNotIn("PROMPT_PRIVATE_LOCK", stderr)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 0)
            self.assertEqual(list(output_root.iterdir()), [])
            self.assertEqual(list(scratch.glob("grokbot2claw-live-*")), [])

            subsequent = subprocess.run(
                ["python3", "-u", "-c", child_code, "same-agent", str(fake)],
                cwd=Path(message.__file__).parent,
                env=second_env,
                text=True,
                capture_output=True,
                timeout=90,
            )
            self.assertEqual(subsequent.returncode, 0, subsequent.stderr)
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
        finally:
            if first is not None and first.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(first.pid, signal.SIGTERM)
                try:
                    first.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(first.pid, signal.SIGKILL)
                    first.wait(timeout=30)
            capture_stack.close()


class DoctorCommandTest(unittest.TestCase):
    def call_main(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = message.main(argv, runner=mock.Mock())
        return status, stdout.getvalue(), stderr.getvalue()

    def test_doctor_all_green_exits_zero(self):
        with mock.patch.object(message.shutil, "which", return_value="/usr/bin/openclaw"):
            status, stdout, stderr = self.call_main(["--doctor"])
        self.assertEqual(status, 0, stderr)
        self.assertNotIn("FAIL", stdout)
        self.assertIn("all checks passed", stdout)
        for label, _ in message.DOCTOR_CHECKS:
            self.assertIn(f"[ok] {label}:", stdout)

    def test_doctor_reports_missing_openclaw_and_fails(self):
        def fake_which(name):
            return None if name == "openclaw" else "/usr/bin/" + name

        with mock.patch.object(message.shutil, "which", side_effect=fake_which):
            status, stdout, stderr = self.call_main(["--doctor"])
        self.assertNotEqual(status, 0)
        self.assertIn("[FAIL] openclaw on PATH:", stdout)
        self.assertIn("install OpenClaw", stdout)
        self.assertIn("one or more checks failed", stderr)

    def test_doctor_reports_missing_sqlite3_and_fails(self):
        def fake_which(name):
            return None if name == "sqlite3" else "/usr/bin/" + name

        with mock.patch.object(message.shutil, "which", side_effect=fake_which):
            status, stdout, stderr = self.call_main(["--doctor"])
        self.assertNotEqual(status, 0)
        self.assertIn("[FAIL] sqlite3 on PATH:", stdout)
        self.assertIn("3.35", stdout)
        self.assertIn("one or more checks failed", stderr)

    def test_doctor_prints_one_line_per_check(self):
        with mock.patch.object(message.shutil, "which", return_value="/usr/bin/openclaw"):
            _, stdout, _ = self.call_main(["--doctor"])
        lines = [line for line in stdout.splitlines() if line.startswith("[")]
        self.assertEqual(len(lines), len(message.DOCTOR_CHECKS))

    def test_doctor_does_not_require_send_or_agent(self):
        with mock.patch.object(message.shutil, "which", return_value="/usr/bin/openclaw"):
            status, _, stderr = self.call_main(["--doctor"])
        self.assertEqual(status, 0, stderr)


class InterruptHandlingTest(unittest.TestCase):
    def test_ctrl_c_during_run_exits_130_not_nameerror(self):
        # Regression: main()'s SIGINT handler used sys.stderr without a
        # module-level `import sys`, so Ctrl-C raised NameError instead of
        # exiting with status 130.
        with (
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as ctx,
            mock.patch.object(
                runtime, "run_once", side_effect=runtime.SignalInterruption(signal.SIGINT)
            ),
            mock.patch("sys.argv", ["runtime.py", "--replay", "--agent", "test-agent"]),
        ):
            runtime.main()
        self.assertEqual(ctx.exception.code, 128 + signal.SIGINT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
