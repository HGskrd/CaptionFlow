"""Opt-in reporting and signed update discovery. No request before consent."""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import threading
import time
import urllib.parse
import urllib.request
from common import redact

BASE_URL = "https://h.gskrd.me/apps/captionflow"
VERSION = "0.3.0"
RELEASE_SEQUENCE = 3
# Release verification key is public. Signing key stays off the VPS and outside the repo.
PUBLIC_KEY = "yTp27s/gWvh5/qBk/I6/IcszfL0oPFANbj1ffM+QDiQ="
FORBIDDEN = {"audio", "pcm", "transcript", "text", "hostname", "serial", "username", "token",
             "diagnostic_tail", "error", "speech_model_path", "translation_model_path"}


def public_report(value):
    if isinstance(value, dict):
        return {k: public_report(v) for k, v in value.items() if k not in FORBIDDEN}
    if isinstance(value, list): return [public_report(v) for v in value]
    if isinstance(value, str): return redact(value)[:2000]
    return value


class Feedback:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "network-consent.json"
        self.lock = threading.RLock()
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"consent": None}

    @property
    def allowed(self): return self.state.get("consent") is True

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state), encoding="utf-8")
        if os.name != "nt": temporary.chmod(0o600)
        temporary.replace(self.path)

    def consent(self, enabled):
        with self.lock:
            self.state["consent"] = bool(enabled)
            self.state["consent_version"] = 1
            self.save()

    def request(self, path, body=None, method=None):
        with self.lock:
            if not self.allowed: raise PermissionError("Local-only mode: internet access is disabled")
            headers = {"Content-Type": "application/json", "User-Agent": "CaptionFlow/" + VERSION}
            if self.state.get("token"): headers["Authorization"] = "Bearer " + self.state["token"]
            request = urllib.request.Request(BASE_URL + path, data=json.dumps(body).encode() if body is not None else None,
                                             headers=headers, method=method)
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)

    def enroll(self):
        with self.lock:
            if not self.allowed: raise PermissionError("Local-only mode")
            if not self.state.get("token"):
                self.state.update(self.request("/v1/enroll", {"consent_version": 1}))
                self.save()

    def upload(self, report, force=True):
        self.enroll()
        # Long, resumable sessions can exceed a whole-report request's size/quota.
        # Send only new terminal trial records, in bounded batches. Running
        # checkpoints stay local until Stop, completion or crash recovery.
        if not force and time.time() - self.state.get("last_report_upload", 0) < 600:
            return
        report = public_report(report)
        sent = self.state.setdefault("uploaded_trials", {})
        pending = []
        for trial in report.get("trials", []):
            if trial.get("status") == "running": continue
            identity = report["run_id"] + ":" + trial["id"]
            digest = hashlib.sha256(json.dumps(trial, sort_keys=True).encode()).hexdigest()
            if sent.get(identity) != digest:
                pending.append((identity, digest, trial))
        for offset in range(0, len(pending), 50):
            batch = pending[offset:offset + 50]
            self.request("/v1/reports", {"schema": 1, "app_version": VERSION,
                         "report": {**report, "trials": [item[2] for item in batch]}})
            with self.lock:
                for identity, digest, _ in batch: sent[identity] = digest
                self.state["last_report_upload"] = time.time()
                self.save()

    def diagnostic(self, machine, phase, error=None):
        self.enroll()
        # Raw exception messages can contain user text or paths. Share a fingerprint,
        # stage and hardware; detailed logs remain in the user-reviewed local ZIP.
        body = {"schema": 1, "app_version": VERSION, "hardware": public_report(machine), "phase": phase,
                "error_fingerprint": hashlib.sha256(str(error).encode()).hexdigest() if error else None}
        self.request("/v1/diagnostics", body)

    def audio_diagnostic(self, metrics):
        self.enroll()
        self.request('/v1/diagnostics', {'schema': 1, 'app_version': VERSION,
                     'phase': 'audio_capture', 'metrics': public_report(metrics)})

    def revoke(self):
        error = None
        with self.lock:
            try:
                if self.allowed and self.state.get("token"):
                    self.request("/v1/self", method="DELETE")
            except Exception as exc: error = exc
            finally:
                self.state = {"consent": False, "consent_version": 1}; self.save()
        if error: raise RuntimeError("Internet access is now disabled. Server deletion could not be confirmed; uploaded reports expire after 30 days.") from error

    def check_update(self):
        target = "windows-x86_64" if os.name == "nt" else "macos-aarch64"
        envelope = self.request("/v1/releases/" + target)
        if not envelope: return None
        release = verify_manifest(envelope, target)
        if release["sequence"] <= RELEASE_SEQUENCE or release["sequence"] < self.state.get("highest_release", 0): return None
        return release

    def download_update(self, release):
        from runtime_assets import download
        with self.lock:
            if not self.allowed: raise PermissionError("Local-only mode")
            # Verified archive goes to a separate folder. Never overwrite a running app.
            path = self.directory / "updates" / Path(urllib.parse.urlparse(release["url"]).path).name
            download(release["url"], path, release["sha256"])
            self.state["highest_release"] = release["sequence"]; self.save()
            return path


def verify_manifest(envelope, target, key=PUBLIC_KEY, now=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    payload = base64.b64decode(envelope["payload"], validate=True)
    Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True)).verify(
        base64.b64decode(envelope["signature"], validate=True), payload)
    release = json.loads(payload)
    if release["platform"] != target or type(release["sequence"]) is not int or release["expires"] < (time.time() if now is None else now):
        raise ValueError("Update platform, sequence or expiration is invalid")
    parsed = urllib.parse.urlparse(release["url"])
    expected = urllib.parse.urlparse(BASE_URL)
    if parsed.scheme != "https" or parsed.netloc != expected.netloc or not parsed.path.startswith(expected.path + "/releases/"):
        raise ValueError("Update URL is outside the pinned release origin")
    if len(release["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in release["sha256"]):
        raise ValueError("Invalid release digest")
    return release
