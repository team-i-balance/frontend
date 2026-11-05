"""팀 서버(backend) API 클라이언트: /health, /process, /process/decide, /process/restore.

탐지·가명화·기록·복원은 서버가 한다 (2026-11-05 결정). 이 PC는 화면과 트리거를 맡는다.

- 표준 라이브러리(urllib)만 쓴다. 회사 프록시 환경 변수(HTTPS_PROXY 등)는 urllib이 그대로 따른다.
- 오류 메시지에 원문을 넣지 않는다. 서버 응답의 detail도 보여 주지 않는다
  (FastAPI 검증 오류 detail에는 요청 원문이 섞여 돌아올 수 있다). 알려진 경우만 우리 문구로 바꾼다.
- 서버에 모델이 없으면(400 + LOCAL_PII_MODEL_DIR) 규칙만 모드로 다시 요청하고, 그 사실을 결과에 남긴다.
- 서버 작업(work_id)은 /process 시점부터 10분 동안만 남고, 복원은 한 번만 된다 (backend sessions.py).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit

GRADES = {"public_O": "O", "sensitive_S": "S", "confidential_C": "C"}
WORK_TTL_SECONDS = 600     # backend app/sessions.py DEFAULT_TTL_SECONDS
MAX_TEXT = 50_000          # backend ProcessIn.text max_length
MODEL_MISSING_HINT = "LOCAL_PII_MODEL_DIR"

# 앱의 사유 → 서버 사유 (서버에는 false_positive / business_need 두 가지뿐)
REASONS = {"false_positive": "false_positive", "business_need": "business_need",
           "approved": "business_need", "none": None, None: None}


class ServerError(Exception):
    """kind: unreachable / no_model / expired / failed / rejected / bad_response"""

    def __init__(self, message: str, kind: str = "failed", status: int | None = None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status


@dataclass
class ProcessResult:
    work_id: str
    masked_text: str
    detected_types: list[str]
    detected_count: int
    grade: str               # O / S / C
    confidence: str          # high(모델+규칙) / low(규칙만) / medium
    remaining_count: int     # 가린 글을 다시 검사했을 때 의심 구간 수
    rules_only: bool         # 서버에 모델이 없어 규칙만으로 검사함
    received_at: float = field(default_factory=time.time)

    def expires_in(self) -> float:
        return WORK_TTL_SECONDS - (time.time() - self.received_at)


@dataclass
class DecideResult:
    outgoing_text: str | None
    restore_available: bool
    queue: str | None


@dataclass
class RestoreResult:
    text: str
    unknown: list[str]
    blocked: list[str]


def is_insecure(url: str) -> bool:
    """암호화되지 않은 연결인가 (이 PC 안의 테스트 서버는 제외)."""
    parts = urlsplit(url)
    return parts.scheme != "https" and (parts.hostname or "").lower() not in ("localhost", "127.0.0.1", "::1")


def _detail(exc: urllib.error.HTTPError):
    try:
        return json.loads(exc.read().decode("utf-8")).get("detail")
    except Exception:
        return None


def _http_error(status: int, detail) -> ServerError:
    text = detail if isinstance(detail, str) else ""
    if status == 400 and MODEL_MISSING_HINT in text:
        return ServerError("서버에 탐지 모델이 설정돼 있지 않아요.", "no_model", status)
    if status == 404:
        return ServerError("서버 작업이 만료됐어요. 검사 후 10분이 지나면 서버에서 지워져요.", "expired", status)
    if status == 422 and "detection failed" in text:
        return ServerError("서버 검사에 실패했어요. 이 글은 검사가 끝나지 않았어요.", "failed", status)
    if status == 422:
        return ServerError("서버가 요청을 받지 않았어요. 글이 비었거나 너무 길어요(최대 5만 자).", "rejected", status)
    if status >= 500:
        return ServerError(f"서버 오류가 났어요 (HTTP {status}).", "failed", status)
    return ServerError(f"서버가 요청을 거절했어요 (HTTP {status}).", "rejected", status)


class ServerClient:
    def __init__(self, base_url: str, timeout: float = 20.0):
        url = (base_url or "").strip().rstrip("/")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("server url must look like https://host[:port]")
        self.base_url = url
        self.timeout = timeout
        self.model_available: bool | None = None  # 모름 / 서버에 모델 있음 / 없음(규칙만)

    @property
    def insecure(self) -> bool:
        return is_insecure(self.base_url)

    def _request(self, method: str, path: str, body=None, timeout: float | None = None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310 (설정한 서버 주소)
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise _http_error(exc.code, _detail(exc)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ServerError("서버에 연결하지 못했어요. 서버 주소와 네트워크를 확인하세요.", "unreachable") from None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ServerError("서버 응답을 읽지 못했어요.", "bad_response") from None

    # ------------------------------------------------------------------ API
    def health(self) -> bool:
        data = self._request("GET", "/health", timeout=min(self.timeout, 5.0))
        return isinstance(data, dict) and data.get("status") == "ok"

    def process(self, text: str) -> ProcessResult:
        rules_only = self.model_available is False
        try:
            data = self._request("POST", "/process", {"text": text, "rules_only": rules_only})
        except ServerError as exc:
            if exc.kind != "no_model" or rules_only:
                raise
            self.model_available = False
            rules_only = True
            data = self._request("POST", "/process", {"text": text, "rules_only": True})
        else:
            if not rules_only:
                self.model_available = True
        try:
            return ProcessResult(
                work_id=str(data["work_id"]), masked_text=str(data["masked_text"]),
                detected_types=[str(t) for t in data.get("detected_types", [])],
                detected_count=int(data.get("detected_count", 0)),
                grade=GRADES.get(data.get("grade"), "S"),
                confidence=str(data.get("detection_confidence", "medium")),
                remaining_count=int(data.get("remaining_count", 0)), rules_only=rules_only)
        except (KeyError, TypeError, ValueError):
            raise ServerError("서버 응답 형식이 달라요.", "bad_response") from None

    def decide(self, work_id: str, action: str, reason: str | None = None) -> DecideResult:
        data = self._request("POST", "/process/decide",
                             {"work_id": work_id, "action": action, "reason": REASONS.get(reason)})
        try:
            return DecideResult(data.get("outgoing_text"), bool(data.get("restore_available")),
                                (data.get("event") or {}).get("queue"))
        except AttributeError:
            raise ServerError("서버 응답 형식이 달라요.", "bad_response") from None

    def restore(self, work_id: str, text: str) -> RestoreResult:
        data = self._request("POST", "/process/restore", {"work_id": work_id, "text": text})
        try:
            return RestoreResult(str(data["text"]), list(data.get("unknown", [])), list(data.get("blocked", [])))
        except (KeyError, TypeError):
            raise ServerError("서버 응답 형식이 달라요.", "bad_response") from None
