import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import message
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
            result = subprocess.run(["python3", str(command), *argv], input="hello", text=True,
                                    capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
        result = subprocess.run(["python3", str(command), "--help"], text=True,
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--send", result.stdout)

    def test_input_validation_happens_before_runner(self):
        runner = mock.Mock()
        cases = [b"", b"\xff", b"x" * (message.MAX_MESSAGE_BYTES + 1), b"bad\x00body", b"bad\x1fbody"]
        for index, data in enumerate(cases):
            with self.subTest(index=index):
                path = self.root / str(index)
                path.write_bytes(data)
                status, stdout, stderr = self.call_main(
                    ["--send", "--agent", "docs-agent", "--message-file", str(path)], runner)
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                self.assertIn("message command:", stderr)
        runner.assert_not_called()

    def test_stdin_and_json_output_are_stable(self):
        reply = "OpenClaw says: ¡listo!"
        result = {"passed": True, "request_id": "fixture-id", "actual_reply": reply,
                  "cleanup": {"port_closed": True}}
        stdin = mock.Mock()
        stdin.buffer = io.BytesIO("line one\n'quotes' $(literal) 世界".encode())
        runner = mock.Mock(return_value=result)
        with mock.patch.object(message.sys, "stdin", stdin):
            status, stdout, stderr = self.call_main(
                ["--send", "--agent", "docs-agent", "--message-file", "-", "--json"], runner)
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(json.loads(stdout), {
            "status": "completed", "request_id": "fixture-id", "reply": reply})
        runner.assert_called_once_with(
            "line one\n'quotes' $(literal) 世界",
            agent="docs-agent", principal="grokbot-macshell")

    def test_output_directory_saves_exact_json_and_prints_compact_receipt(self):
        reply = "line one\n`code` \"quotes\" — 世界"
        result = {"passed": True, "request_id": "fixture-id", "actual_reply": reply,
                  "cleanup": {"port_closed": True}}
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        output_root = self.root / "results with spaces 世界"

        status, stdout, stderr = self.call_main([
            "--send", "--agent", "docs-agent", "--message-file", str(message_path),
            "--output-dir", str(output_root), "--json",
        ], mock.Mock(return_value=result))

        self.assertEqual((status, stderr), (0, ""))
        receipt = json.loads(stdout)
        self.assertEqual(set(receipt), {"status", "request_id", "result_path", "sha256"})
        self.assertNotIn(reply, stdout)
        result_path = Path(receipt["result_path"])
        data = result_path.read_bytes()
        self.assertEqual(json.loads(data), {
            "status": "completed", "request_id": "fixture-id", "reply": reply})
        self.assertEqual(receipt["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(os.stat(result_path.parent).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(result_path).st_mode & 0o777, 0o600)

    def test_invalid_output_directory_is_rejected_before_runner(self):
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        invalid = self.root / "not-a-directory"
        invalid.write_text("existing user file")
        runner = mock.Mock()

        status, stdout, stderr = self.call_main([
            "--send", "--agent", "docs-agent", "--message-file", str(message_path),
            "--output-dir", str(invalid),
        ], runner)

        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "message command: cannot prepare output directory\n")
        self.assertEqual(invalid.read_text(), "existing user file")
        runner.assert_not_called()

    def test_output_directory_is_removed_when_bridge_fails(self):
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        output_root = self.root / "results"
        runner = mock.Mock(return_value={
            "passed": False, "error_code": "adapter_failed", "cleanup": {"port_closed": True}})

        status, stdout, stderr = self.call_main([
            "--send", "--agent", "docs-agent", "--message-file", str(message_path),
            "--output-dir", str(output_root),
        ], runner)

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "message command failed: adapter_failed\n")
        self.assertEqual(list(output_root.iterdir()), [])

    def test_output_directory_is_removed_when_runner_raises(self):
        message_path = self.root / "message.txt"
        message_path.write_text("safe request")
        output_root = self.root / "results"

        status, stdout, stderr = self.call_main([
            "--send", "--agent", "docs-agent", "--message-file", str(message_path),
            "--output-dir", str(output_root),
        ], mock.Mock(side_effect=TimeoutError("private detail")))

        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "message command failed: internal failure\n")
        self.assertEqual(list(output_root.iterdir()), [])

    def test_failure_has_no_stdout_or_internal_detail(self):
        path = self.root / "message.txt"
        path.write_text("safe request")
        for runner, expected in (
            (mock.Mock(return_value={
                "passed": False, "final_status": "failed", "error_code": "adapter_failed",
                "failure": "token=private /private/path", "cleanup": {"port_closed": True},
            }), "message command failed: adapter_failed\n"),
            (mock.Mock(side_effect=TimeoutError("token=private /private/path")),
             "message command failed: internal failure\n"),
        ):
            with self.subTest(expected=expected):
                status, stdout, stderr = self.call_main(
                    ["--send", "--agent", "docs-agent", "--message-file", str(path)], runner)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, expected)

    def test_real_http_bridge_adapter_fixture_preserves_body_and_invokes_once(self):
        capture = self.root / "capture.json"
        counter = self.root / "count"
        reply = "fixture custom reply — not ACK"
        fake = self.root / "openclaw"
        fake.write_text(textwrap.dedent(f"""\
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
        """))
        fake.chmod(0o700)
        body = "First line\n¡Hola, 世界! 'quotes' $(touch never) `false`; & |"
        result = run_once(
            body, agent="separate-agent", real_openclaw=str(fake), expected_reply=reply)
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
        fake.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json
            from pathlib import Path
            Path({str(counter)!r}).write_text("1")
            print(json.dumps({{"ok": False, "error": {{"type": "cli_error"}}}}))
            raise SystemExit(1)
        """))
        fake.chmod(0o700)
        result = run_once(
            "harmless failure fixture", agent="separate-agent", real_openclaw=str(fake))
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_code"], "adapter_failed")
        self.assertEqual(counter.read_text(), "1")
        self.assertEqual(result["cli_invocations"], 1)
        self.assertTrue(all(result["cleanup"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
