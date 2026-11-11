"""CaptionFlow opt-in ingestion. Bind loopback behind the existing HTTPS tunnel."""
import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import urllib.parse

PREFIX = "/apps/captionflow"
STATE = Path(os.environ.get("CAPTIONFLOW_STATE", "/var/lib/captionflow"))
MAX_BODY = 2 * 1024 * 1024
LOCK = threading.Lock()
RATE = {}
FORBIDDEN = {"audio", "pcm", "transcript", "text", "hostname", "serial", "username", "token", "diagnostic_tail", "error"}


@contextmanager
def connection():
    conn = sqlite3.connect(STATE / "reports.sqlite", timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            yield conn
    finally:
        conn.close()


def initialize():
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "releases").mkdir(exist_ok=True)
    with connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE, created REAL)")
        conn.execute("CREATE TABLE IF NOT EXISTS reports (digest TEXT, client TEXT REFERENCES clients(id) ON DELETE CASCADE, kind TEXT, received REAL, payload TEXT, PRIMARY KEY(digest,client))")


def expire_reports():
    while True:
        with connection() as conn:
            conn.execute("DELETE FROM reports WHERE received < ?", (time.time() - 30 * 86400,))
        time.sleep(3600)


def validate(value, depth=0):
    if depth > 12: raise ValueError("Too deeply nested")
    if isinstance(value, dict):
        if any(k.casefold() in FORBIDDEN for k in value): raise ValueError("Private fields are not accepted")
        for k, v in value.items():
            if len(k) > 100: raise ValueError("Invalid key")
            validate(v, depth + 1)
    elif isinstance(value, list):
        if len(value) > 2000: raise ValueError("Too many entries")
        for item in value: validate(item, depth + 1)
    elif isinstance(value, str) and len(value) > 2000:
        raise ValueError("String too long")


class Handler(BaseHTTPRequestHandler):
    server_version = "CaptionFlow"
    def log_message(self, *_): pass  # No IP, token or payload in access logs.
    def setup(self):
        super().setup(); self.connection.settimeout(20)
    def respond(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def auth(self, conn):
        value = self.headers.get("Authorization", "")
        if not value.startswith("Bearer "): return None
        row = conn.execute("SELECT id FROM clients WHERE token_hash=?", (hashlib.sha256(value[7:].encode()).hexdigest(),)).fetchone()
        return row[0] if row else None
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == PREFIX + "/health": return self.respond(200, {"status": "ok", "schema": 1})
        if path.startswith(PREFIX + "/v1/releases/"):
            target = path.rsplit("/", 1)[-1]
            if target not in ("windows-x86_64", "macos-aarch64"): return self.respond(404, {})
            file = STATE / "releases" / (target + ".json")
            return self.respond(200, json.loads(file.read_text()) if file.exists() else None)
        if path.startswith(PREFIX + "/releases/"):
            name = path[len(PREFIX + "/releases/"):]
            if "/" in name or "\\" in name or not name.endswith(".zip") or name.startswith("."):
                return self.respond(404, {})
            file = STATE / "releases" / name
            if not file.is_file(): return self.respond(404, {})
            self.send_response(200); self.send_header("Content-Type", "application/zip")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(file.stat().st_size)); self.end_headers()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""): self.wfile.write(chunk)
            return
        self.respond(404, {})
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > MAX_BODY: return self.respond(413, {"error_code": "body_size"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json": return self.respond(415, {})
            # Tunnel supplies CF-Connecting-IP; hash only for this in-memory rate bucket.
            ip = self.headers.get("CF-Connecting-IP", self.client_address[0])
            bucket = (hashlib.sha256(ip.encode()).hexdigest(), int(time.time() // 60))
            with LOCK:
                for key in list(RATE):
                    if key[1] < bucket[1]: del RATE[key]
                RATE[bucket] = RATE.get(bucket, 0) + 1
                if RATE[bucket] > 30: return self.respond(429, {"error_code": "rate_limit"})
            value = json.loads(self.rfile.read(size), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
            if not isinstance(value, dict): raise ValueError("Expected object")
            with connection() as conn:
                conn.execute("DELETE FROM reports WHERE received < ?", (time.time() - 30 * 86400,))
                if path == PREFIX + "/v1/enroll":
                    if value != {"consent_version": 1}: return self.respond(400, {})
                    if conn.execute("SELECT count(*) FROM clients").fetchone()[0] > 10000: return self.respond(503, {})
                    identity = secrets.token_hex(16); token = secrets.token_urlsafe(32)
                    conn.execute("INSERT INTO clients VALUES (?,?,?)", (identity, hashlib.sha256(token.encode()).hexdigest(), time.time()))
                    conn.commit()
                    return self.respond(201, {"client_id": identity, "token": token})
                client = self.auth(conn)
                if not client: return self.respond(401, {})
                if path not in (PREFIX + "/v1/reports", PREFIX + "/v1/diagnostics"): return self.respond(404, {})
                validate(value)
                if value.get("schema") != 1: return self.respond(400, {})
                if conn.execute("SELECT coalesce(sum(length(payload)),0) FROM reports").fetchone()[0] > 500 * 1024 * 1024:
                    return self.respond(503, {"error_code": "storage_quota"})
                if conn.execute("SELECT count(*) FROM reports WHERE client=? AND received>?", (client, time.time() - 86400)).fetchone()[0] >= 200:
                    return self.respond(429, {"error_code": "daily_quota"})
                payload = json.dumps(value, sort_keys=True, allow_nan=False)
                digest = hashlib.sha256(payload.encode()).hexdigest()
                conn.execute("INSERT OR IGNORE INTO reports VALUES (?,?,?,?,?)", (digest, client, path.rsplit("/",1)[-1], time.time(), payload))
                conn.commit()
                self.respond(201, {"accepted": True, "digest": digest})
        except (ValueError, TypeError, KeyError): self.respond(400, {"error_code": "invalid_report"})
    def do_DELETE(self):
        if self.path != PREFIX + "/v1/self": return self.respond(404, {})
        with connection() as conn:
            client = self.auth(conn)
            if not client: return self.respond(401, {})
            conn.execute("DELETE FROM clients WHERE id=?", (client,))
        self.respond(200, {"deleted": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--port", type=int, default=5052)
    parser.add_argument("--export", help="Export received reports locally; administrator SSH access required")
    args = parser.parse_args(); initialize()
    if args.export:
        with connection() as conn:
            rows = [{"kind": kind, "received": received, "payload": json.loads(payload)} for kind, received, payload in conn.execute("SELECT kind,received,payload FROM reports ORDER BY received")]
        Path(args.export).write_text(json.dumps(rows, indent=2))
        print(f"Exported {len(rows)} submissions")
    else:
        threading.Thread(target=expire_reports, daemon=True).start()
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
