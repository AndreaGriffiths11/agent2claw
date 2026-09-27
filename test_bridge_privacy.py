"""Regression tests for bridge.sh prompt privacy.

The bridge must never place message text on a command line, where other
local users could read it via ps(1). invoke_agent() stages the prompt in a
private (0600) file and hands the adapter only the path.
"""

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

BRIDGE = Path(__file__).with_name("bridge.sh")


class BridgePromptPrivacyTest(unittest.TestCase):
    # Hostile prompt: shell metacharacters, quotes, newlines, tab, unicode.
    SECRET = "top-secret $(touch pwned) `backtick` 'sq' \"dq\"\nline2 ¡Hola, 世界!\ttab"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.adapters = self.root / "adapters"
        self.adapters.mkdir()
        self.argv_capture = self.root / "argv.txt"
        self.content_capture = self.root / "content.bin"
        self.workdir = self.root / "workdir"
        self.workdir.mkdir()

        # Stub adapter: records its argv, the prompt file's mode, and the
        # exact prompt bytes it received; prints a canned reply.
        stub = self.adapters / "stub.sh"
        stub.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                set -uo pipefail
                i=0
                for arg in "$@"; do
                    printf 'arg%d=<%s>\\n' "$i" "$arg" >>{self.argv_capture}
                    i=$((i + 1))
                done
                if [ -n "${{1-}}" ] && [ -f "$1" ]; then
                    mode=$(python3 -c 'import os, stat, sys; info = os.stat(sys.argv[1], follow_symlinks=False); print("prompt_file_mode=%03o" % stat.S_IMODE(info.st_mode))' "$1")
                    printf '%s\\n' "$mode" >>{self.argv_capture}
                    cat "$1" >{self.content_capture}
                fi
                printf 'stub reply'
                """
            )
        )
        stub.chmod(0o700)

        # Extract invoke_agent() verbatim from bridge.sh and drive it with
        # stubbed dependencies (log, STATE, ADAPTERS, ADAPTER_TIMEOUT).
        function = subprocess.run(
            ["sed", "-n", "/^invoke_agent(){/,/^}$/p", str(BRIDGE)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertTrue(function.startswith("invoke_agent(){"), "invoke_agent not extracted")
        self.assertTrue(function.rstrip().endswith("}"), "invoke_agent extraction truncated")
        self.driver = self.root / "driver.sh"
        self.driver.write_text(
            "#!/usr/bin/env bash\n"
            "set -uo pipefail\n"
            "log(){ :; }\n"
            f'STATE="{self.state}"\n'
            f'ADAPTERS="{self.adapters}"\n'
            'ADAPTER_TIMEOUT="25"\n' + function + 'invoke_agent "$@"\n'
        )

    def tearDown(self):
        self.temp.cleanup()

    def run_invoke_agent(self, agent, prompt):
        return subprocess.run(
            ["bash", str(self.driver), agent, prompt],
            capture_output=True,
            timeout=30,
            cwd=self.workdir,
        )

    def test_prompt_text_never_appears_on_adapter_argv(self):
        result = self.run_invoke_agent("stub", self.SECRET)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"stub reply")
        argv_text = self.argv_capture.read_text(encoding="utf-8")
        # The adapter must receive exactly one argument: the prompt file path.
        arg_lines = [line for line in argv_text.splitlines() if line.startswith("arg")]
        self.assertEqual(len(arg_lines), 1)
        prompt_path = arg_lines[0][arg_lines[0].index("<") + 1 : -1]
        self.assertTrue(Path(prompt_path).name.startswith("prompt.stub."))
        # No part of the secret may leak onto the command line.
        self.assertNotIn("top-secret", argv_text)
        self.assertNotIn("¡Hola", argv_text)

    def test_prompt_file_is_private_and_byte_exact(self):
        result = self.run_invoke_agent("stub", self.SECRET)
        self.assertEqual(result.returncode, 0, result.stderr)
        argv_text = self.argv_capture.read_text(encoding="utf-8")
        self.assertIn("prompt_file_mode=600", argv_text)
        self.assertEqual(self.content_capture.read_bytes(), self.SECRET.encode("utf-8"))

    def test_prompt_metacharacters_are_not_evaluated(self):
        result = self.run_invoke_agent("stub", self.SECRET)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.workdir / "pwned").exists(), "command substitution ran")

    def test_prompt_file_is_removed_after_invocation(self):
        result = self.run_invoke_agent("stub", self.SECRET)
        self.assertEqual(result.returncode, 0, result.stderr)
        leftovers = list(self.state.glob("prompt.*"))
        self.assertEqual(leftovers, [], f"prompt file leaked: {leftovers}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
