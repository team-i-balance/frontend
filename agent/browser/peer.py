"""Authenticated local IPC used by the terminal composer."""

import hmac
import json
import secrets
import urllib.error
import urllib.request

from .. import config
from .actions import ChipError
from .bridge import PORT, signature


class Peer:
    def __init__(self, key=None, port=PORT):
        self.key = key or config.load_settings().get("browser_key")
        if not self.key:
            raise ChipError("KP Guard 앱의 설정에서 ‘브라우저·CLI 연결 코드 복사’를 먼저 누르세요.")
        self.base = f"http://127.0.0.1:{port}/v1/"
        self.channel = secrets.token_hex(16)
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, path, body, headers):
        req = urllib.request.Request(
            self.base + path, data=body, headers={"Content-Type": "application/json", **headers}
        )
        try:
            with self.http.open(req, timeout=35) as res:
                return json.load(res)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get("error")
            except (ValueError, AttributeError):
                detail = None
            raise ChipError(detail or "KP Guard 연결을 다시 확인해 주세요.") from None
        except (ValueError, OSError):
            raise ChipError("KP Guard 앱에 연결할 수 없습니다.") from None

    def __call__(self, action, **params):
        nonce = secrets.token_hex(16)
        hello = self.post("challenge", json.dumps({"nonce": nonce}).encode(), {})
        challenge = hello.get("challenge", "")
        expected = signature(self.key, f"server\n{nonce}\n{challenge}".encode())
        if not hmac.compare_digest(expected, hello.get("proof", "")):
            raise ChipError("KP Guard 연결 코드가 일치하지 않습니다.")
        body = json.dumps({"action": action, "channel": self.channel, **params}, ensure_ascii=False).encode()
        proof = signature(self.key, b"client\n" + challenge.encode() + b"\n" + body)
        return self.post("action", body, {"X-KP-Challenge": challenge, "X-KP-Proof": proof})
