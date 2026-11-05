"""테스트용 가짜 팀 서버. backend의 /health, /process, /process/decide, /process/restore,
/files/sanitize, /files/{work_id}/download와 같은 형식으로 답한다.

탐지는 단순하다: 전화번호(규칙), 정해 둔 이름(모델 모드에서만), api_key= 값(규칙).
실제 backend처럼 모델이 없으면 rules_only=false 요청에 400을, 파일 요청에 503 MODEL_UNAVAILABLE을 주고,
작업은 한 번 복원하면 지우고, 보호본은 한 번만 내준다. busy=n이면 파일 요청 n번에 429를 준다.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

PHONE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
KEY = re.compile(r"api_key=(\S+)")
NAMES = ("김민수", "이지은", "홍길동")
LABELS = {"PHONE": "전화번호", "NAME": "이름", "API_KEY": "API키"}
SECRET = {"API_KEY"}


class FakeBackend:
    def __init__(self, model: bool = True, busy: int = 0):
        self.model = model
        self.busy = busy
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
                m = re.fullmatch(r"/files/([^/]+)/download", self.path)
                if self.path == "/health":
                    self._send(200, {"status": "ok", "detection": "model" if backend.model else "rules_only"})
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
                if self.path == "/files/sanitize":
                    status, out = backend.handle_file(self.headers.get("Content-Type", ""), raw)
                    self._send(status, out, [("Cache-Control", "no-store")]
                               + ([("Retry-After", "1")] if status == 429 else []))
                    return
                status, out = backend.handle(self.path, json.loads(raw.decode("utf-8")))
                self._send(status, out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
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
