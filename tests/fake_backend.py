"""테스트용 가짜 팀 서버. backend의 /health, /process, /process/decide, /process/restore,
/files/sanitize, /files/{work_id}/download와 같은 형식으로 답한다.

탐지는 단순하다: 전화번호(규칙), 정해 둔 이름(모델 모드에서만), api_key= 값(규칙).
실제 backend처럼 모델이 없으면 rules_only=false 요청에 400을, 파일 요청에 503 MODEL_UNAVAILABLE을 주고,
작업은 한 번 복원하면 지우고, 보호본은 한 번만 내준다. busy=n이면 파일 요청 n번에 429를 준다.

auth=True면 backend#10처럼 /health·/auth/dev-token 말고는 Bearer 토큰을 확인한다 (없거나 틀리면 401).
개발용 토큰은 메모리에만 두고 restart()에서 잊는다. dev_tokens=False면 발급을 403으로 막는다 (운영 서버).
auth=False 서버에는 /auth/dev-token이 없다 (인증 전 backend). tls="server"면 tests/certs의 인증서로 HTTPS를 연다
("server"는 시험 CA가 서명, "other"는 자체 서명). use_cert()로 인증서를 바꿔 끼울 수 있다.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import ssl
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CERTS = Path(__file__).resolve().parent / "certs"   # 시험 전용 인증서·키 (127.0.0.1)
PUBLIC = ("/health", "/auth/dev-token")


def fingerprint(name: str) -> str:
    """tests/certs/<name>.pem 인증서의 SHA-256 지문 (소문자 16진수)."""
    der = ssl.PEM_cert_to_DER_cert((CERTS / f"{name}.pem").read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

PHONE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
KEY = re.compile(r"api_key=(\S+)")
NAMES = ("김민수", "이지은", "홍길동")
LABELS = {"PHONE": "전화번호", "NAME": "이름", "API_KEY": "API키"}
SECRET = {"API_KEY"}


class FakeBackend:
    def __init__(self, model: bool = True, busy: int = 0, legacy_health: bool = False, *, auth: bool = False,
                 dev_tokens: bool = True, team_tokens=(), tls: str | None = None):
        self.model = model
        self.busy = busy
        self.legacy_health = legacy_health  # backend#11 전 서버: /health가 탐지 범위를 알려 주지 않음
        self.auth = auth
        self.dev_tokens = dev_tokens
        self.team_tokens = set(team_tokens)
        self.issued: set[str] = set()       # /auth/dev-token으로 내준 토큰 (서버 메모리)
        self.policy = True                  # False면 GET /policy/current가 없는 서버
        self.redirect_to = ""               # GET /redirect가 보낼 주소
        self.auth_seen: list[tuple[str, str | None]] = []  # (경로, Authorization 헤더)
        self.works: dict[str, dict] = {}
        self.decisions: list[dict] = []
        self.requests: list[str] = []  # 경로만 남긴다
        self.received: list[str] = []  # /process로 받은 글 (서버로 무엇이 갔는지 테스트에서 확인)
        self.uploads: list[tuple[str, bytes]] = []  # /files/sanitize로 받은 (파일 이름, 바이트)
        self.files: dict[str, bytes] = {}           # 아직 내려받지 않은 보호본
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorized(self) -> bool:
                """backend app/auth.py require_api처럼 Bearer 토큰을 확인한다. 막았으면 401을 보내고 False."""
                header = self.headers.get("Authorization")
                backend.auth_seen.append((self.path, header))
                if not backend.auth or self.path in PUBLIC:
                    return True
                scheme, _, token = (header or "").partition(" ")
                if scheme.lower() != "bearer" or not token:
                    self._send(401, {"detail": "missing bearer token"}, [("WWW-Authenticate", "Bearer")])
                    return False
                if token not in backend.team_tokens | backend.issued:
                    self._send(401, {"detail": "invalid token"}, [("WWW-Authenticate", "Bearer")])
                    return False
                return True

            def _send(self, status, body, headers=()):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                backend.requests.append(self.path)
                if not self._authorized():
                    return
                m = re.fullmatch(r"/files/([^/]+)/download", self.path)
                if self.path == "/policy/current" and backend.policy:
                    self._send(200, {"active": False})
                elif self.path == "/redirect":  # 다른 주소로 보내는 응답 (토큰이 따라가면 안 된다)
                    self.send_response(307)
                    self.send_header("Location", backend.redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path == "/health":
                    self._send(200, {"status": "ok"} if backend.legacy_health else
                               {"status": "ok", "detection": "model" if backend.model else "rules_only",
                                "slm": False, "slm_ready": False})
                elif m:
                    data = backend.files.pop(m.group(1), None)
                    if data is None:
                        self._send(404, {"detail": "FILE_NOT_AVAILABLE"}, [("Cache-Control", "no-store")])
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", XLSX)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send(404, {"detail": "Not Found"})

            def do_POST(self):
                backend.requests.append(self.path)
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if not self._authorized():
                    return
                if self.path == "/auth/dev-token":
                    if not backend.auth:  # 인증 전 backend에는 이 경로가 없다
                        self._send(404, {"detail": "Not Found"})
                    elif not backend.dev_tokens:
                        self._send(403, {"detail": "dev token issuance is disabled"})
                    else:
                        token = secrets.token_urlsafe(32)
                        backend.issued.add(token)
                        self._send(200, {"token": token, "scope": "api",
                                         "usage": "send as 'Authorization: Bearer <token>'"})
                    return
                if self.path == "/files/sanitize":
                    status, out = backend.handle_file(self.headers.get("Content-Type", ""), raw)
                    self._send(status, out, [("Cache-Control", "no-store")]
                               + ([("Retry-After", "1")] if status == 429 else []))
                    return
                status, out = backend.handle(self.path, json.loads(raw.decode("utf-8")))
                self._send(status, out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.handle_error = lambda request, address: None  # 시험에서 일부러 끊는 TLS 연결은 조용히
        self.tls = None
        if tls:
            self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.use_cert(tls)
            # 핸드셰이크는 요청 처리 스레드에서 (끊긴 연결이 서버 전체를 막지 않게)
            self.httpd.socket = self.tls.wrap_socket(self.httpd.socket, server_side=True,
                                                     do_handshake_on_connect=False)
        scheme = "https" if tls else "http"
        self.url = f"{scheme}://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()

    start = __enter__

    def stop(self):
        self.__exit__()

    def use_cert(self, name: str) -> None:
        """다음 연결부터 tests/certs/<name>.pem 인증서를 내보인다 (서버 인증서 교체 흉내)."""
        self.tls.load_cert_chain(CERTS / f"{name}.pem", CERTS / f"{name}.key")

    def restart(self) -> None:
        """서버 다시 시작: 메모리에 있던 개발용 토큰을 잊는다 (backend는 배포마다 다시 시작한다)."""
        self.issued.clear()

    # ------------------------------------------------------------------ API
    def handle(self, path: str, body: dict):
        if path == "/process":
            text = body.get("text", "")
            self.received.append(text)
            if not text:  # FastAPI 검증 오류처럼 원문(input)을 detail에 담아 돌려준다
                return 422, {"detail": [{"type": "string_too_short", "loc": ["body", "text"], "input": text}]}
            if not body.get("rules_only") and not self.model:
                return 400, {"detail": "full detection requires LOCAL_PII_MODEL_DIR to be set on the server; "
                                       "use rules_only=true otherwise"}
            mapping = {}
            masked, kinds, count = _mask(text, mapping, {}, names=not body.get("rules_only"))
            grade = "confidential_C" if kinds & SECRET else ("sensitive_S" if kinds else "public_O")
            work_id = uuid.uuid4().hex
            self.works[work_id] = {"text": text, "masked": masked, "mapping": mapping}
            return 200, {"work_id": work_id, "masked_text": masked, "detected_types": sorted(kinds),
                         "detected_count": count, "grade": grade,
                         "detection_confidence": "low" if body.get("rules_only") else "high", "remaining_count": 0}
        if path == "/process/decide":
            work = self.works.pop(body["work_id"], None)
            if work is None:
                return 404, {"detail": "unknown or expired work_id"}
            self.decisions.append({"action": body["action"], "reason": body.get("reason")})
            keep = body["action"] == "send_transformed"
            if keep:
                self.works[body["work_id"]] = work
            outgoing = {"send_transformed": work["masked"], "cancel": None}.get(body["action"], work["text"])
            queue = "pre_warning" if body.get("reason") is None else "violation_candidate"
            return 200, {"event": {"queue": queue}, "outgoing_text": outgoing, "restore_available": keep}
        if path == "/process/restore":
            work = self.works.pop(body["work_id"], None)
            if work is None:
                return 404, {"detail": "unknown or expired work_id (did you call /decide with send_transformed?)"}
            reverse = {token: (kind, value) for (kind, value), token in work["mapping"].items()}
            unknown, blocked = [], []

            def swap(m):
                token = m.group()
                if token not in reverse:
                    unknown.append(token)
                    return token
                kind, value = reverse[token]
                if kind in SECRET:
                    blocked.append(token)
                    return token
                return value

            text = re.sub(r"\[[^\[\]\r\n]+_\d+\]", swap, body["text"])
            return 200, {"text": text, "unknown": unknown, "blocked": blocked}
        return 404, {"detail": "Not Found"}

    def handle_file(self, content_type: str, raw: bytes):
        """backend app/file_router.py처럼 FileResult로 답한다 (오류도)."""
        work_id = uuid.uuid4().hex

        def failed(status, reason):
            return status, {"work_id": work_id, "status": "FAILED", "grade": None, "reason": reason,
                            "summary": None, "expires_at": None}

        if self.busy > 0:
            self.busy -= 1
            return failed(429, "FILE_PROCESSING_BUSY")
        m = re.match(r"multipart/form-data;\s*boundary=(\S+)", content_type)
        if not m:
            return failed(415, "EXPECTED_MULTIPART")
        part = _file_part(raw, m.group(1).encode())
        if part is None:
            return failed(422, "EXPECTED_ONE_FILE")
        filename, data = part
        self.uploads.append((filename, data))
        if not filename.lower().endswith(".xlsx"):
            return failed(415, "UNSUPPORTED_FILE_TYPE")
        if not self.model:
            return failed(503, "MODEL_UNAVAILABLE")
        from agent.files.xlsx.sanitizer import sanitize_xlsx

        result = sanitize_xlsx(data, work_id, _FileProcessor(), filename=filename)
        if result.status == "FAILED":
            return failed(422, result.reason)
        if result.status == "BLOCKED":
            return 200, {"work_id": work_id, "status": "BLOCKED", "grade": "confidential_C",
                         "reason": result.reason, "summary": None, "expires_at": None}
        self.files[work_id] = result.file_bytes
        s = result.summary
        return 200, {"work_id": work_id, "status": "READY",
                     "grade": "sensitive_S" if result.grade == "S" else "public_O", "reason": None,
                     "summary": {"changed_cells": s.changed_cells, "removed_comments": s.removed_comments,
                                 "removed_sheets": s.removed_sheets, "removed_rows": s.removed_rows,
                                 "removed_columns": s.removed_columns},
                     "expires_at": "2026-11-05T00:10:00Z"}


def _mask(text: str, mapping: dict, counters: dict, names: bool = True):
    """→ (가린 글, 찾은 종류, 가린 곳 수). mapping·counters를 같이 쓰면 같은 값은 같은 자리표시가 된다."""
    spans = [(m.start(), m.end(), "PHONE") for m in PHONE.finditer(text)]
    spans += [(m.start(1), m.end(1), "API_KEY") for m in KEY.finditer(text)]
    if names:
        for name in NAMES:
            spans += [(m.start(), m.end(), "NAME") for m in re.finditer(name, text)]
    out, pos, kinds, count = [], 0, set(), 0
    for s, e, kind in sorted(spans):
        if s < pos:
            continue
        key = (kind, text[s:e])
        if key not in mapping:
            counters[kind] = counters.get(kind, 0) + 1
            mapping[key] = f"[{LABELS[kind]}_{counters[kind]}]"
        out += [text[pos:s], mapping[key]]
        pos = e
        kinds.add(kind)
        count += 1
    out.append(text[pos:])
    return "".join(out), kinds, count


def _file_part(raw: bytes, boundary: bytes):
    """multipart 본문에서 name="file" 파트 하나를 꺼낸다 → (파일 이름, 바이트). 하나가 아니면 None."""
    found = []
    for chunk in raw.split(b"--" + boundary)[1:]:
        if chunk.startswith(b"--"):
            break
        head, _, body = chunk.partition(b"\r\n\r\n")
        m = re.search(rb'name="([^"]*)"; filename="([^"]*)"', head)
        if m:
            found.append((m.group(1), m.group(2).decode("utf-8"), body[:-2] if body.endswith(b"\r\n") else body))
    if len(found) != 1 or found[0][0] != b"file":
        return None
    return found[0][1], found[0][2]


class _FileProcessor:
    """file_sanitizer Processor: 칸마다 _mask를 쓰고, 파일 하나 안에서는 같은 값에 같은 자리표시를 준다."""

    def __init__(self):
        self.mapping, self.counters = {}, {}

    def process(self, session_id, segments):
        from agent.files.xlsx.sanitizer import Decision

        out = {}
        for seg in segments:
            masked, kinds, _ = _mask(seg.text, self.mapping, self.counters)
            grade = "C" if kinds & SECRET else ("S" if kinds else "O")
            out[seg.id] = Decision(grade, masked if seg.exportable else None)
        return out

    def verify(self, session_id, segments):
        return all(not _mask(s.text, {}, {})[1] for s in segments)
