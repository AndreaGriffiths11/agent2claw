import hashlib
import http.client
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import http_server


MESSAGE_SCHEMA = """
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


class HTTPTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "messages.db"
        with sqlite3.connect(self.db) as db:
            db.executescript(MESSAGE_SCHEMA)
        self.tokens = {"muse": secrets.token_urlsafe(32), "other": secrets.token_urlsafe(32)}
        self.auth = self.root / "auth.json"
        self.auth.write_text(json.dumps({"principals": {
            "muse": {"token_sha256": self.digest("muse"), "recipients": ["rusty"]},
            "other": {"token_sha256": self.digest("other"), "recipients": ["rusty"]},
        }}))
        self.auth.chmod(0o600)
        self.server = http_server.create_server(
            "127.0.0.1", 0, str(self.db), "testteam", str(self.auth),
            read_timeout=0.15, db_timeout=0.1, max_connections=8,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def digest(self, name):
        return hashlib.sha256(self.tokens[name].encode("ascii")).hexdigest()

    def request(self, method, path, value=None, token="muse", headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(value).encode() if value is not None else None)
        base_headers = {}
        if token is not None:
            base_headers["Authorization"] = "Bearer " + self.tokens.get(token, token)
        if body is not None:
            base_headers["Content-Type"] = "application/json"
        if headers:
            base_headers.update(headers)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, body=body, headers=base_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, json.loads(data) if data else None

    def message(self, key="request-1", body="hello", recipient="rusty"):
        return {"recipient": recipient, "body": body, "idempotency_key": key}

    def test_submit_and_get_pending(self):
        status, sent = self.request("POST", "/messages", self.message())
        self.assertEqual((status, sent["status"]), (202, "pending"))
        status, result = self.request("GET", "/messages/" + sent["id"])
        self.assertEqual(status, 200)
        self.assertEqual(result["sender"], "muse")
        self.assertEqual(result["recipient"], "rusty")
        self.assertIsNone(result["reply"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT from_agent,to_agent,body FROM messages").fetchone(), ("muse", "rusty", "hello"))

    def test_authentication_and_identity_isolation(self):
        self.assertEqual(self.request("POST", "/messages", self.message(), token=None)[0], 401)
        self.assertEqual(self.request("POST", "/messages", self.message(), token="wrong")[0], 401)
        status, sent = self.request("POST", "/messages", self.message())
        self.assertEqual(status, 202)
        self.assertEqual(self.request("GET", "/messages/" + sent["id"], token="other")[0], 404)
        impersonated = dict(self.message("request-2"), sender="rusty")
        self.assertEqual(self.request("POST", "/messages", impersonated)[0], 400)

    def test_recipient_and_input_validation(self):
        self.assertEqual(self.request("POST", "/messages", self.message(recipient="other"))[0], 403)
        self.assertEqual(self.request("POST", "/messages", self.message(body="x" * (http_server.MAX_MESSAGE_BYTES + 1)))[0], 400)
        self.assertEqual(self.request("POST", "/messages", raw=b"{")[0], 400)
        self.assertEqual(self.request("POST", "/messages", self.message(), headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("POST", "/messages", self.message(key="bad key"))[0], 400)
        self.assertEqual(self.request("GET", "/messages/not-an-id")[0], 404)
        self.assertEqual(self.request("GET", "/messages?token=x")[0], 404)
        self.assertEqual(self.request("PUT", "/messages", self.message())[0], 405)
        status, _ = self.request("POST", "/messages", raw=b"", headers={"Content-Length": str(http_server.MAX_REQUEST_BYTES + 1)})
        self.assertEqual(status, 413)

    def test_transfer_encoding_rejected(self):
        status, result = self.request("POST", "/messages", self.message(), headers={"Transfer-Encoding": "chunked"})
        self.assertEqual((status, result["error"]), (400, "invalid_request"))

    def test_idempotency_and_concurrent_retries(self):
        def send(_):
            return self.request("POST", "/messages", self.message("same-key"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(send, range(8)))
        ids = {result[1]["id"] for result in results}
        self.assertEqual(len(ids), 1)
        self.assertIn(202, {result[0] for result in results})
        self.assertTrue({result[0] for result in results}.issubset({200, 202}))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(self.request("POST", "/messages", self.message("same-key", "different"))[0], 409)

    def test_database_busy_is_temporary_error_without_detail_leak(self):
        lock = sqlite3.connect(self.db, timeout=0)
        lock.execute("BEGIN EXCLUSIVE")
        try:
            status, result = self.request("POST", "/messages", self.message("busy"))
        finally:
            lock.rollback()
            lock.close()
        self.assertEqual((status, result), (503, {"error": "temporarily_unavailable"}))
        self.assertNotIn(str(self.db), json.dumps(result))

    def test_partial_body_times_out(self):
        client = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        client.sendall((
            "POST /messages HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            "Authorization: Bearer " + self.tokens["muse"] + "\r\n"
            "Content-Type: application/json\r\nContent-Length: 10\r\n\r\n{"
        ).encode())
        time.sleep(0.25)
        response = client.recv(4096)
        client.close()
        self.assertIn(b" 408 ", response)

    def test_auth_file_must_be_private(self):
        self.auth.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "mode 0600"):
            http_server.load_auth(str(self.auth))

    def test_status_transitions_do_not_overwrite_completion(self):
        _, sent = self.request("POST", "/messages", self.message())
        with sqlite3.connect(self.db) as db:
            message_id = db.execute("SELECT message_id FROM http_requests WHERE id=?", (sent["id"],)).fetchone()[0]
        old_stdin = http_server.sys.stdin
        try:
            class Input:
                buffer = __import__("io").BytesIO(b"fixture")
            http_server.sys.stdin = Input()
            http_server.update_status(str(self.db), message_id, "completed")
            http_server.update_status(str(self.db), message_id, "failed", "adapter_failed")
        finally:
            http_server.sys.stdin = old_stdin
        _, result = self.request("GET", "/messages/" + sent["id"])
        self.assertEqual((result["status"], result["reply"]), ("completed", "fixture"))

    def test_bridge_end_to_end_uses_body_as_data(self):
        adapters = self.root / "adapters"
        scripts = self.root / "scripts"
        state = self.root / "state"
        bin_dir = self.root / "bin"
        for directory in (adapters, scripts, state, bin_dir):
            directory.mkdir()
        capture = self.root / "captured.txt"
        marker = self.root / "must-not-exist"
        (adapters / "rusty.sh").write_text("#!/bin/sh\nprintf '%s' \"$1\" > \"$CAPTURE\"\nprintf '%s' 'synthetic fixture reply'\n")
        (adapters / "rusty.sh").chmod(0o700)
        (bin_dir / "timeout").write_text("#!/bin/sh\nshift\nexec \"$@\"\n")
        (bin_dir / "timeout").chmod(0o700)
        (scripts / "send.sh").write_text("""#!/bin/sh
set -eu
team=$1 from=$2 to=$3
shift 3
[ \"$1\" = --body ] && [ \"$2\" = - ]
body=$(cat)
python3 - \"$AGMSG_DB\" \"$team\" \"$from\" \"$to\" \"$body\" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute('INSERT INTO messages(team,from_agent,to_agent,body) VALUES(?,?,?,?)', sys.argv[2:6])
PY
""")
        (scripts / "send.sh").chmod(0o700)
        dangerous = "literal $(touch %s); `false`; 'quotes'" % marker
        _, sent = self.request("POST", "/messages", self.message("bridge-e2e", dangerous))
        env = os.environ.copy()
        env.update({
            "AGMSG_DB": str(self.db), "AGMSG_SCRIPTS": str(scripts),
            "AGMSG_BRIDGE_TEAM": "testteam", "AGMSG_BRIDGE_AGENTS": "rusty",
            "AGMSG_BRIDGE_ADAPTERS": str(adapters), "AGMSG_BRIDGE_STATE": str(state),
            "AGMSG_BRIDGE_POLL": "0.05", "CAPTURE": str(capture),
            "PATH": str(bin_dir) + os.pathsep + env["PATH"],
        })
        process = subprocess.Popen(
            ["bash", str(Path(__file__).with_name("bridge.sh"))], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            deadline = time.time() + 5
            result = None
            while time.time() < deadline:
                _, result = self.request("GET", "/messages/" + sent["id"])
                if result["status"] in ("completed", "failed"):
                    break
                time.sleep(0.05)
        finally:
            process.terminate()
            output = process.communicate(timeout=2)[0]
        self.assertEqual((result["status"], result["reply"]), ("completed", "synthetic fixture reply"), output)
        self.assertIn(dangerous, capture.read_text())
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
