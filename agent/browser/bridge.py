"""Opt-in loopback bridge. Pairing key and team credentials never cross into a web page."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .actions import ChipError
from ..server.client import ServerError

PORT = 47681
MAX_BODY = 1024 * 1024
EXTENSION = re.compile(r"(?:chrome-extension://[a-p]{32}|moz-extension://[a-fA-F0-9-]{36})")
NONCE = re.compile(r"[0-9a-f]{32}")


def signature(key, message):
    return hmac.new(key.encode("ascii"), message, hashlib.sha256).hexdigest()


class BrowserBridge(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, key, actions, port=PORT):
        self.key, self.actions = key, actions
        self.challenges = {}
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)

    def challenge(self, nonce):
        if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
            raise ChipError("연결 코드 확인에 실패했습니다.")
        with self.lock:
            now = time.monotonic()
            self.challenges = {k: end for k, end in self.challenges.items() if end > now}
            if len(self.challenges) >= 512:
                raise ChipError("잠시 뒤 다시 연결해 주세요.")
            challenge = secrets.token_hex(16)
            self.challenges[challenge] = now + 20
        proof = signature(self.key, f"server\n{nonce}\n{challenge}".encode())
        return {"challenge": challenge, "proof": proof}

    def authenticated(self, challenge, proof, body):
        if not NONCE.fullmatch(challenge) or not re.fullmatch(r"[0-9a-f]{64}", proof):
            return False
        expected = signature(self.key, b"client\n" + challenge.encode() + b"\n" + body)
        if not hmac.compare_digest(expected, proof):
            return False
        with self.lock:
            return self.challenges.pop(challenge, 0) > time.monotonic()

    def start(self):
        threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()
        self.actions.clear()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Never log drafts, keys or request targets.

    def permitted(self):
        origin = self.headers.get("Origin", "")
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}" and (
            not origin or EXTENSION.fullmatch(origin)
        )

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if EXTENSION.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-KP-Challenge, X-KP-Proof")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.reply(200 if self.permitted() else 403, {})

    def do_POST(self):
        if not self.permitted():
            return self.reply(403, {})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {})
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 < length <= MAX_BODY:
                return self.reply(413, {})
            body = self.rfile.read(length)
            if self.path == "/v1/action" and not self.server.authenticated(
                self.headers.get("X-KP-Challenge", ""), self.headers.get("X-KP-Proof", ""), body
            ):
                return self.reply(403, {"error": "브라우저를 다시 연결해 주세요."})
            data = json.loads(body)
            if not isinstance(data, dict):
                return self.reply(400, {})
            if self.path == "/v1/challenge":
                result = self.server.challenge(data.get("nonce"))
            elif self.path == "/v1/action":
                result = self.server.actions.handle(data)
            else:
                return self.reply(404, {})
        except (ChipError, ServerError) as exc:
            return self.reply(400, {"error": str(exc)})
        except (ValueError, TypeError, UnicodeError):
            return self.reply(400, {"error": "요청 형식을 확인해 주세요."})
        except Exception:
            return self.reply(500, {"error": "KP Guard에서 요청을 처리하지 못했습니다."})
        self.reply(200, result)
