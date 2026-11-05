"""테스트용 가짜 팀 서버. backend의 /health, /process, /process/decide, /process/restore와 같은 형식으로 답한다.

탐지는 단순하다: 전화번호(규칙), 정해 둔 이름(모델 모드에서만), api_key= 값(규칙).
실제 backend처럼 모델이 없으면 rules_only=false 요청에 400을 주고, 작업은 한 번 복원하면 지운다.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PHONE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
KEY = re.compile(r"api_key=(\S+)")
NAMES = ("김민수", "이지은", "홍길동")
LABELS = {"PHONE": "전화번호", "NAME": "이름", "API_KEY": "API키"}
SECRET = {"API_KEY"}


class FakeBackend:
    def __init__(self, model: bool = True):
        self.model = model
        self.works: dict[str, dict] = {}
        self.decisions: list[dict] = []
        self.requests: list[str] = []  # 경로만 남긴다
        self.received: list[str] = []  # /process로 받은 글 (서버로 무엇이 갔는지 테스트에서 확인)
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                backend.requests.append(self.path)
                if self.path == "/health":
                    self._send(200, {"status": "ok"})
                else:
                    self._send(404, {"detail": "Not Found"})

            def do_POST(self):
                backend.requests.append(self.path)
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                status, out = backend.handle(self.path, body)
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
            spans = [(m.start(), m.end(), "PHONE") for m in PHONE.finditer(text)]
            spans += [(m.start(1), m.end(1), "API_KEY") for m in KEY.finditer(text)]
            if not body.get("rules_only"):
                for name in NAMES:
                    spans += [(m.start(), m.end(), "NAME") for m in re.finditer(name, text)]
            spans.sort()
            mapping, counters, out, pos = {}, {}, [], 0
            for s, e, kind in spans:
                key = (kind, text[s:e])
                if key not in mapping:
                    counters[kind] = counters.get(kind, 0) + 1
                    mapping[key] = f"[{LABELS[kind]}_{counters[kind]}]"
                out += [text[pos:s], mapping[key]]
                pos = e
            out.append(text[pos:])
            kinds = {k for _, _, k in spans}
            grade = "confidential_C" if kinds & SECRET else ("sensitive_S" if kinds else "public_O")
            work_id = uuid.uuid4().hex
            self.works[work_id] = {"text": text, "masked": "".join(out), "mapping": mapping}
            return 200, {"work_id": work_id, "masked_text": "".join(out), "detected_types": sorted(kinds),
                         "detected_count": len(spans), "grade": grade,
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
