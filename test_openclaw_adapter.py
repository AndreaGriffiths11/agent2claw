import ast
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import runtime

ADAPTER = Path(__file__).with_name("adapters") / "openclaw.sh"


class OpenClawAdapterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture.json"
        self.fake = self.root / "openclaw"
        self.fake.write_text(
            textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os, stat, sys, time
            from pathlib import Path
            args = sys.argv[1:]
            path = args[args.index("--message-file") + 1]
            if path == "-":
                print(json.dumps({"ok": False, "error": {
                    "type": "cli_error", "message": "Message file not found: -"}}))
                raise SystemExit(1)
            info = os.stat(path)
            assert stat.S_ISREG(info.st_mode)
            assert stat.S_IMODE(info.st_mode) == 0o600
            assert info.st_uid == os.getuid()
            prompt = Path(path).read_text(encoding="utf-8")
            assert sys.stdin.read() == ""
            with open(os.environ["CAPTURE"], "w", encoding="utf-8") as f:
                json.dump({"args": args, "prompt": prompt, "path": path,
                           "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid}, f)
            if os.environ.get("MODE") == "hang":
                time.sleep(30)
            mode = os.environ.get("MODE", "success")
            if "RESPONSE" in os.environ:
                print(os.environ["RESPONSE"])
                raise SystemExit(int(os.environ.get("EXIT_CODE", "0")))
            if mode == "stderr":
                print("fixture diagnostic", file=sys.stderr)
            if mode == "failed_exit":
                print(json.dumps({
                    "ok": False,
                    "error": {
                        "type": "cli_error",
                        "message": "gateway token=supersecretvalue12345678901234567890 at ws://127.0.0.1:9999/private from /Users/example/.openclaw/config.json",
                    },
                }))
                raise SystemExit(1)
            if mode == "malformed":
                print("not json")
            elif mode == "empty":
                print(json.dumps({"status": "ok", "result": {"payloads": []}}))
            elif mode == "timeout":
                print(json.dumps({"runId": "fixture-timeout", "status": "timeout",
                    "summary": "aborted", "result": {"payloads": [{"text": "partial"}],
                    "meta": {"stopReason": "timeout", "timeoutPhase": "provider"}}}))
            elif mode == "error":
                print(json.dumps({"ok": False, "error": {"type": "cli_error"}}))
            elif mode == "oversized":
                print(json.dumps({"status": "ok", "result": {"payloads": [{"text": "x" * 100}]}}))
            else:
                # Gateway serializer: principal-CeDW0csN.js:1690-1698.
                print(json.dumps({"runId": "fixture-run", "status": "ok",
                    "summary": "completed", "result": {"payloads": [
                        {"text": "first block", "mediaUrl": None},
                        {"text": "second block"}
                    ], "meta": {"durationMs": 1, "agentMeta": {
                        "sessionId": "fixture", "model": "synthetic", "provider": "fixture"}}}}))
        """)
        )
        self.fake.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def run_adapter(self, mode="success", adapter=ADAPTER, **overrides):
        env = os.environ.copy()
        env.update(
            {
                "OPENCLAW_BIN": str(self.fake),
                "OPENCLAW_AGENT": "rusty",
                "OPENCLAW_SESSION_KEY": "agmsg-bridge-test",
                "OPENCLAW_TIMEOUT": "17",
                "CAPTURE": str(self.capture),
                "TMPDIR": str(self.root),
                "MODE": mode,
            }
        )
        env.update(overrides)
        result = subprocess.run(
            ["bash", str(adapter), "literal $(touch nope); `false`; 'quotes'\n¡Hola, 世界!"],
            env=env,
            text=True,
            capture_output=True,
            timeout=3,
        )
        if overrides.get("OPENCLAW_AGENT") != "--deliver":
            self.assertTrue(self.capture.exists(), "fake CLI must inspect the prompt file")
        self.assertEqual(
            list(self.root.glob("grokbot2claw-openclaw*")), [], "temporary file leaked"
        )
        if self.capture.exists():
            captured = json.loads(self.capture.read_text())
            self.assertFalse(Path(captured["path"]).exists(), "prompt file leaked")
            self.assertEqual(captured["mode"], 0o600)
            self.assertEqual(captured["uid"], os.getuid())
            self.assertNotIn(captured["path"], result.stdout + result.stderr)
        return result

    def test_invocation_is_fixed_isolated_json_and_non_delivering(self):
        result = self.run_adapter()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "first block\nsecond block")
        captured = json.loads(self.capture.read_text())
        self.assertEqual(
            captured["args"],
            [
                "agent",
                "--agent",
                "rusty",
                "--session-key",
                "agmsg-bridge-test",
                "--message-file",
                captured["path"],
                "--timeout",
                "17",
                "--json",
            ],
        )
        self.assertNotIn("--deliver", captured["args"])
        self.assertIn("untrusted external message", captured["prompt"])
        self.assertTrue(
            captured["prompt"].endswith(
                "\n\nliteral $(touch nope); `false`; 'quotes'\n¡Hola, 世界!"
            )
        )
        self.assertNotIn(captured["prompt"], captured["args"])
        self.assertFalse(any("literal" in arg for arg in captured["args"]))

    def test_fake_cli_rejects_literal_dash_instead_of_reading_stdin(self):
        result = subprocess.run(
            [str(self.fake), "agent", "--message-file", "-"],
            input="old fixture incorrectly accepted this stdin prompt",
            text=True,
            capture_output=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["message"], "Message file not found: -")
        self.assertFalse(self.capture.exists())

    def test_process_timeout_removes_private_prompt_file(self):
        # Fixture-only deadline: allow Python startup before the deliberate hang.
        # No production timeout change or real CLI involved.
        source = ADAPTER.read_text()
        self.assertEqual(source.count("timeout=int(timeout) + 5"), 1)
        adapter = self.root / "quick-timeout.sh"
        adapter.write_text(source.replace("timeout=int(timeout) + 5", "timeout=1.0"))
        result = self.run_adapter("hang", adapter=adapter)
        self.assertTrue(self.capture.exists(), "fake CLI must open the prompt before timeout")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("CLI process timed out", result.stderr)
        self.assertIn("exit 124", result.stderr)

    def test_generated_live_wrapper_forwards_file_and_scrubs_proof(self):
        tree = ast.parse(ADAPTER.parent.parent.joinpath("runtime.py").read_text())
        wrappers = [
            node.args[1].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "write_executable"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and "AGMSG_REAL_OPENCLAW" in str(node.args[1].value)
        ]
        self.assertEqual(len(wrappers), 1)
        compile(wrappers[0], "generated-wrapper", "exec")
        wrapper = self.root / "wrapper"
        wrapper.write_text(wrappers[0])
        wrapper.chmod(0o700)
        proof_path = self.root / "proof.json"
        for mode in ("success", "failed_exit", "nested_error"):
            with self.subTest(mode=mode):
                extra = {}
                if mode == "nested_error":
                    extra["RESPONSE"] = json.dumps(
                        self.gateway(
                            error={"kind": "provider_error", "message": "token=syntheticsecret"}
                        )
                    )
                result = self.run_adapter(
                    mode,
                    OPENCLAW_BIN=str(wrapper),
                    AGMSG_REAL_OPENCLAW=str(self.fake),
                    AGMSG_LIVE_RUN_MARKER=str(self.root / ("marker-" + mode)),
                    AGMSG_LIVE_PROOF=str(proof_path),
                    **extra,
                )
                self.assertEqual(result.returncode, 0 if mode == "success" else 1)
                captured = json.loads(self.capture.read_text())
                proof_text = proof_path.read_text()
                proof = json.loads(proof_text)
                self.assertNotIn(captured["path"], proof_text)
                self.assertNotIn(captured["prompt"], proof_text)
                self.assertEqual(
                    proof["argv"],
                    [
                        "agent",
                        "--agent",
                        "rusty",
                        "--session-key",
                        "agmsg-bridge-test",
                        "--message-file",
                        "[private prompt file]",
                        "--timeout",
                        "17",
                        "--json",
                    ],
                )
                self.assertEqual(
                    proof["prompt_file"],
                    {
                        "regular_file": True,
                        "mode": "0o600",
                        "owned_by_current_user": True,
                    },
                )
                if mode == "success":
                    self.assertEqual(proof["session_id"], "fixture")
                    self.assertEqual(proof["model"], "synthetic")
                    self.assertEqual(proof["provider"], "fixture")
                if mode == "nested_error":
                    self.assertEqual(proof["error_type"], "provider_error")
                if mode == "failed_exit":
                    self.assertNotIn("supersecretvalue", proof_text)
                self.assertNotIn("error_message", proof)

    def gateway(self, **meta):
        # Minimal Gateway success, not agent exec or --local.
        # principal-CeDW0csN.js:1690-1698; observed RETRY-2.md.
        return {
            "runId": "synthetic-run",
            "status": "ok",
            "summary": "completed",
            "result": {"payloads": [{"text": "synthetic reply", "mediaUrl": None}], "meta": meta},
        }

    def assert_rejected(self, value, **env):
        result = self.run_adapter(RESPONSE=json.dumps(value), **env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        return result

    def test_gateway_success_without_top_level_ok(self):
        for status in ("ok", "completed"):
            value = self.gateway()
            value["status"] = status
            result = self.run_adapter(RESPONSE=json.dumps(value))
            self.assertEqual((result.returncode, result.stdout), (0, "synthetic reply"))
        value["ok"] = True
        result = self.run_adapter(RESPONSE=json.dumps(value))
        self.assertEqual((result.returncode, result.stdout), (0, "synthetic reply"))

    def test_reply_trailing_newlines_are_preserved(self):
        value = self.gateway()
        value["result"]["payloads"] = [{"text": "exact reply\n\n", "mediaUrl": None}]
        result = self.run_adapter(RESPONSE=json.dumps(value))
        self.assertEqual((result.returncode, result.stdout), (0, "exact reply\n\n"))

    def test_outer_failure_wins_over_nested_text(self):
        for status in (
            "error",
            "timeout",
            "in_flight",
            "failed",
            "aborted",
            "pending",
            None,
            [],
            {},
        ):
            with self.subTest(status=status):
                value = self.gateway()
                value["status"] = status
                self.assert_rejected(value)
        value = self.gateway()
        value["ok"] = False
        self.assert_rejected(value)
        value = self.gateway()
        value["error"] = {}
        self.assert_rejected(value)
        self.assert_rejected(self.gateway(), EXIT_CODE="1")

    def test_nested_terminal_failures_never_become_replies(self):
        # Actual Gateway metadata readers: principal-CeDW0csN.js:1670-1683.
        for meta in (
            {"error": {"kind": "provider_error", "message": "fixture failure"}},
            {"aborted": True},
            {"yielded": True},
            {"stopReason": "error"},
            {"stopReason": "timeout"},
            {"stopReason": "aborted"},
            {"stopReason": "restart"},
            {"stopReason": "superseded"},
            {"timeoutPhase": "provider"},
            {"livenessState": "blocked"},
            {"livenessState": "abandoned"},
        ):
            with self.subTest(meta=meta):
                self.assert_rejected(self.gateway(**meta))

    def test_nested_error_kind_is_bounded_and_redacted(self):
        value = self.gateway(
            error={
                "kind": "provider_error",
                "message": "token=syntheticsecret at https://example.test/private /Users/example/private",
            }
        )
        value["status"] = "error"
        result = self.assert_rejected(value)
        self.assertIn("type=provider_error", result.stderr)
        self.assertNotIn("syntheticsecret", result.stderr)
        self.assertNotIn("example.test", result.stderr)
        value["result"]["meta"]["error"]["kind"] = "unsafe kind " * 100
        value["result"]["meta"]["error"]["message"] = "fixture message " * 100
        result = self.assert_rejected(value)
        self.assertIn("type=unknown", result.stderr)
        self.assertLessEqual(len(result.stderr), 500)

    def test_malformed_and_wrong_runtime_shapes_fail_closed(self):
        for value in (
            [],
            None,
            {"payloads": [{"text": "legacy"}]},
            {"ok": True, "payloads": [{"text": "exec"}]},
            {"status": "ok", "result": []},
        ):
            self.assert_rejected(value)
        for meta in ([], None, {"aborted": "false"}, {"stopReason": []}):
            value = self.gateway()
            value["result"]["meta"] = meta
            self.assert_rejected(value)
        for payloads in (None, {}, "text", [{"text": "   "}]):
            value = self.gateway()
            value["result"]["payloads"] = payloads
            self.assert_rejected(value)
        value = self.gateway()
        value["result"] = {"result": value["result"]}
        self.assert_rejected(value)

    def test_payloads_are_strict_final_text(self):
        for item in (
            None,
            [],
            "text",
            {},
            {"text": 3},
            {"mediaUrl": "fixture"},
            {"text": "no", "isError": True},
            {"text": "no", "isReasoning": True},
            {"text": "no", "isCommentary": True},
            {"text": "no", "mediaUrls": ["fixture"]},
        ):
            with self.subTest(item=item):
                value = self.gateway()
                value["result"]["payloads"].append(item)
                self.assert_rejected(value)

    def test_smoke_requires_one_explicit_mode(self):
        for flags in ([], ["--live", "--replay"]):
            result = subprocess.run(
                ["python3", str(ADAPTER.parent.parent / "runtime.py"), *flags],
                text=True,
                capture_output=True,
                timeout=3,
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")

    def test_http_replay_uses_actual_adapter_without_live_cli(self):
        # The replay has separate 10-second request and process-cleanup bounds;
        # allow both to expire on slower hosted macOS runners.
        result = subprocess.run(
            [
                "python3",
                str(ADAPTER.parent.parent / "runtime.py"),
                "--replay",
                "--agent",
                "test-agent",
            ],
            text=True,
            capture_output=True,
            timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        proof = json.loads(result.stdout)
        self.assertTrue(proof["passed"])
        self.assertEqual(proof["final_status"], "completed")
        self.assertEqual(proof["actual_reply"], proof["expected_reply"])
        self.assertEqual(proof["real_cli_invocations"], 0)
        self.assertTrue(proof["mailbox_reply_exact"])
        self.assertFalse(proof["model_run_proven"])
        self.assertTrue(all(proof["cleanup"].values()))
        self.assertEqual(proof["cli_proof"]["argv"][4], "grokbot2claw-replay")

    def test_reaped_process_survives_denied_macos_signal_probe(self):
        process = mock.Mock(pid=123, poll=mock.Mock(return_value=0))
        with mock.patch.object(runtime.os, "killpg", side_effect=[None, PermissionError]):
            self.assertTrue(runtime.stop_process_group(process))
        process.wait.assert_called_once_with(timeout=10)

    def test_stderr_is_not_mixed_into_reply(self):
        result = self.run_adapter("stderr")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "first block\nsecond block")
        self.assertIn("fixture diagnostic", result.stderr)

    def test_command_failure_emits_no_reply(self):
        result = self.run_adapter("failed_exit")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("exit 1; type=cli_error", result.stderr)
        self.assertNotIn("supersecretvalue", result.stderr)
        self.assertNotIn("127.0.0.1", result.stderr)
        self.assertNotIn("/Users/example", result.stderr)

    def test_malformed_json_fails(self):
        result = self.run_adapter("malformed")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("malformed JSON", result.stderr)

    def test_empty_payloads_fail(self):
        result = self.run_adapter("empty")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("no text reply", result.stderr)

    def test_timeout_and_error_envelopes_fail(self):
        for mode in ("timeout", "error"):
            with self.subTest(mode=mode):
                result = self.run_adapter(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn("OpenClaw command failed", result.stderr)
                self.assertIn(
                    "type=" + ("cli_error" if mode == "error" else "unknown"), result.stderr
                )

    def test_response_limit_fails_closed(self):
        result = self.run_adapter("oversized", OPENCLAW_MAX_JSON_BYTES="50")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("exceeded JSON limit", result.stderr)

    def test_invalid_operator_configuration_is_rejected_before_invocation(self):
        result = self.run_adapter(OPENCLAW_AGENT="--deliver")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("invalid agent id", result.stderr)
        self.assertFalse(self.capture.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
