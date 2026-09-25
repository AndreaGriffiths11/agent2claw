#!/usr/bin/env python3
"""Authenticated, loopback-only HTTP ingress for an agmsg SQLite mailbox."""

import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import sqlite3
import stat
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 16 * 1024
MAX_MESSAGE_BYTES = 8 * 1024
MAX_REPLY_BYTES = 8 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
ID_RE = re.compile(
    r"^/messages/([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)
REQUIRED_MESSAGE_COLUMNS = {"id", "team", "from_agent", "to_agent", "body", "created_at", "read_at"}
STATUS_ERRORS = {
    "adapter_failed",
    "bot_hop_limited",
    "no_reply",
    "rate_limited",
    "reply_delivery_failed",
    "reply_too_large",
    "invalid_reply",
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_secure_db_file(db_path):
    """Pre-create db_path with owner-only 0600 permissions if it doesn't exist yet.

    sqlite3.connect() creates the database file using the process umask, which can
    leave it group/world readable. Pre-creating the file with explicit 0600 mode
    closes that window; existing files are left untouched.
    """
    path = os.fspath(db_path)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return
    os.close(fd)


def connect(db_path, timeout):
    ensure_secure_db_file(db_path)
    db = sqlite3.connect(db_path, timeout=timeout)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return db


def ensure_schema(db_path, timeout):
    with connect(db_path, timeout) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
        if not REQUIRED_MESSAGE_COLUMNS.issubset(columns):
            raise ValueError("database does not contain the supported agmsg messages schema")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS http_requests (
              id TEXT PRIMARY KEY,
              message_id INTEGER NOT NULL UNIQUE,
              principal TEXT NOT NULL,
              recipient TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','processing','completed','failed')),
              reply TEXT,
              error_code TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(principal, idempotency_key)
            )
            """
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_http_message_id ON http_requests(message_id)")


def load_auth(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("auth file must be an owner-owned regular file with mode 0600")
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    principals = raw.get("principals") if isinstance(raw, dict) else None
    if not isinstance(principals, dict) or not principals:
        raise ValueError("auth file must define a non-empty principals object")
    result = {}
    hashes = set()
    for name, item in principals.items():
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or not isinstance(item, dict):
            raise ValueError("invalid principal")
        digest = item.get("token_sha256")
        recipients = item.get("recipients")
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest in hashes
        ):
            raise ValueError("token_sha256 values must be unique lowercase SHA-256 digests")
        if (
            not isinstance(recipients, list)
            or not recipients
            or len(set(recipients)) != len(recipients)
        ):
            raise ValueError("each principal needs unique recipients")
        if any(
            not isinstance(value, str) or not NAME_RE.fullmatch(value) or value == name
            for value in recipients
        ):
            raise ValueError("invalid recipient")
        hashes.add(digest)
        result[name] = {"digest": digest, "recipients": frozenset(recipients)}
    return result


def authenticate(header_values, principals):
    if not header_values or len(header_values) != 1:
        return None
    value = header_values[0]
    if not value.startswith("Bearer "):
        return None
    token = value[7:]
    if not token or len(token) > 512 or not token.isascii():
        return None
    supplied = hashlib.sha256(token.encode("ascii")).hexdigest()
    found = None
    for name, item in principals.items():
        if hmac.compare_digest(supplied, item["digest"]):
            found = name
    return found


def submit(db_path, timeout, team, principal, recipient, body, key):
    fingerprint = hashlib.sha256((recipient + "\0" + body).encode("utf-8")).hexdigest()
    now = utc_now()
    request_id = str(uuid.uuid4())
    try:
        with connect(db_path, timeout) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id,payload_sha256,status FROM http_requests WHERE principal=? AND idempotency_key=?",
                (principal, key),
            ).fetchone()
            if existing:
                if not hmac.compare_digest(existing["payload_sha256"], fingerprint):
                    return 409, {"error": "idempotency_conflict"}
                return 200, {"id": existing["id"], "status": existing["status"]}
            cursor = db.execute(
                "INSERT INTO messages(team,from_agent,to_agent,body,created_at) VALUES(?,?,?,?,?)",
                (team, principal, recipient, body, now),
            )
            db.execute(
                """INSERT INTO http_requests
                   (id,message_id,principal,recipient,idempotency_key,payload_sha256,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,'pending',?,?)""",
                (request_id, cursor.lastrowid, principal, recipient, key, fingerprint, now, now),
            )
        return 202, {"id": request_id, "status": "pending"}
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            return 503, {"error": "temporarily_unavailable"}
        raise


def get_request(db_path, timeout, request_id, principal):
    try:
        with connect(db_path, timeout) as db:
            row = db.execute(
                """SELECT id,principal,recipient,status,reply,error_code,created_at,updated_at
                   FROM http_requests WHERE id=? AND principal=?""",
                (request_id, principal),
            ).fetchone()
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            return 503, {"error": "temporarily_unavailable"}
        raise
    if not row:
        return 404, {"error": "not_found"}
    return 200, {
        "id": row["id"],
        "sender": row["principal"],
        "recipient": row["recipient"],
        "status": row["status"],
        "reply": row["reply"],
        "error": row["error_code"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def update_status(db_path, message_id, status_value, error_code=None):
    reply = None
    if status_value == "completed":
        data = sys.stdin.buffer.read(MAX_REPLY_BYTES + 1)
        if len(data) > MAX_REPLY_BYTES:
            status_value, error_code = "failed", "reply_too_large"
        else:
            try:
                reply = data.decode("utf-8")
            except UnicodeDecodeError:
                status_value, error_code = "failed", "invalid_reply"
            else:
                # Same control-character rule as message.py's read_message: the
                # bridge validates replies before calling --update-status, but
                # this keeps the check in place in-process, defense-in-depth.
                if not reply or "\x00" in reply or "\x1f" in reply:
                    raise ValueError("reply is empty or contains unsupported control characters")
    if status_value == "failed" and error_code not in STATUS_ERRORS:
        raise ValueError("invalid error code")
    allowed_from = ("pending",) if status_value == "processing" else ("pending", "processing")
    placeholders = ",".join("?" for _ in allowed_from)
    with connect(db_path, 2.0) as db:
        db.execute(
            "UPDATE http_requests SET status=?,reply=?,error_code=?,updated_at=? "
            f"WHERE message_id=? AND status IN ({placeholders})",
            (status_value, reply, error_code, utc_now(), message_id) + allowed_from,
        )


class BoundedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_queue_size = 16

    def __init__(self, address, handler, max_connections=8):
        self._slots = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        # BaseHTTPRequestHandler parses the request line and headers before
        # do_POST/do_GET, so apply the deadline before handing off the socket.
        request.settimeout(self.read_timeout)
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "agmsg-http/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def _json(self, status_code, value):
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _principal(self):
        principal = authenticate(self.headers.get_all("Authorization"), self.server.principals)
        if principal is None:
            self.close_connection = True
            self._json(401, {"error": "unauthorized"})
        return principal

    def do_POST(self):
        if urlsplit(self.path).path != "/messages" or self.path != "/messages":
            self._json(404, {"error": "not_found"})
            return
        principal = self._principal()
        if principal is None:
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            self._json(400, {"error": "invalid_request"})
            return
        content_types = self.headers.get_all("Content-Type") or []
        if len(content_types) != 1:
            self._json(415, {"error": "unsupported_media_type"})
            return
        parts = [part.strip().lower() for part in content_types[0].split(";")]
        if parts[0] != "application/json" or any(part != "charset=utf-8" for part in parts[1:]):
            self._json(415, {"error": "unsupported_media_type"})
            return
        lengths = self.headers.get_all("Content-Length") or []
        if len(lengths) != 1:
            self.close_connection = True
            self._json(411, {"error": "length_required"})
            return
        try:
            length = int(lengths[0])
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self.close_connection = True
            self._json(413 if length > MAX_REQUEST_BYTES else 400, {"error": "invalid_request"})
            return
        try:
            raw = self.rfile.read(length)
        except (socket.timeout, TimeoutError):
            self.close_connection = True
            self._json(408, {"error": "request_timeout"})
            return
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "invalid_json"})
            return
        if not isinstance(value, dict) or set(value) != {"recipient", "body", "idempotency_key"}:
            self._json(400, {"error": "invalid_request"})
            return
        recipient, body, key = value["recipient"], value["body"], value["idempotency_key"]
        if (
            not isinstance(recipient, str)
            or recipient not in self.server.principals[principal]["recipients"]
        ):
            self._json(403, {"error": "recipient_forbidden"})
            return
        if (
            not isinstance(body, str)
            or not body
            or len(body.encode("utf-8")) > MAX_MESSAGE_BYTES
            or "\x00" in body
            or "\x1f" in body
        ):
            self._json(400, {"error": "invalid_body"})
            return
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            self._json(400, {"error": "invalid_idempotency_key"})
            return
        code, response = submit(
            self.server.db_path,
            self.server.db_timeout,
            self.server.team,
            principal,
            recipient,
            body,
            key,
        )
        self._json(code, response)

    def do_GET(self):
        match = ID_RE.fullmatch(self.path)
        if not match:
            self._json(404, {"error": "not_found"})
            return
        principal = self._principal()
        if principal is None:
            return
        code, response = get_request(
            self.server.db_path, self.server.db_timeout, match.group(1), principal
        )
        self._json(code, response)

    def _method_not_allowed(self):
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _method_not_allowed


def create_server(
    host, port, db_path, team, auth_file, read_timeout=5.0, db_timeout=2.0, max_connections=8
):
    if host != "127.0.0.1":
        raise ValueError("only the loopback address 127.0.0.1 is permitted")
    if not NAME_RE.fullmatch(team):
        raise ValueError("invalid team")
    principals = load_auth(auth_file)
    ensure_schema(db_path, db_timeout)
    server = BoundedHTTPServer((host, port), Handler, max_connections)
    server.db_path = db_path
    server.team = team
    server.principals = principals
    server.read_timeout = read_timeout
    server.db_timeout = db_timeout
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--team")
    parser.add_argument("--auth-file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--read-timeout", type=float, default=5.0)
    parser.add_argument("--db-timeout", type=float, default=2.0)
    parser.add_argument("--max-connections", type=int, default=8)
    parser.add_argument("--update-status", type=int, metavar="MESSAGE_ID")
    parser.add_argument("--status", choices=("processing", "completed", "failed"))
    parser.add_argument("--error", choices=sorted(STATUS_ERRORS))
    args = parser.parse_args()
    try:
        if args.update_status is not None:
            if not args.status or (args.status == "failed") != bool(args.error):
                parser.error("--update-status requires --status and --error only for failed status")
            update_status(args.db, args.update_status, args.status, args.error)
            return
        if args.status or args.error:
            parser.error("--status/--error require --update-status")
        if not args.team or not args.auth_file:
            parser.error("server mode requires --team and --auth-file")
        if (
            not (0 <= args.port <= 65535)
            or args.read_timeout <= 0
            or args.db_timeout <= 0
            or not (1 <= args.max_connections <= 64)
        ):
            parser.error("invalid server limit")
        server = create_server(
            args.host,
            args.port,
            args.db,
            args.team,
            args.auth_file,
            args.read_timeout,
            args.db_timeout,
            args.max_connections,
        )
        server_host, server_port = server.server_address
        print(f"agmsg HTTP listening on http://{server_host}:{server_port}", flush=True)
        server.serve_forever()
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"agmsg HTTP startup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
