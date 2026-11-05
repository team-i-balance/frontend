"""팀 서버(backend) API 클라이언트: /health, /process, /process/decide, /process/restore,
/files/sanitize, /files/{work_id}/download.

탐지·가명화·기록·복원은 서버가 한다 (2026-11-05 결정). 이 PC는 화면과 트리거를 맡는다.

- 표준 라이브러리(urllib)만 쓴다. 회사 프록시 환경 변수(HTTPS_PROXY 등)는 urllib이 그대로 따른다.
- 오류 메시지에 원문을 넣지 않는다. 서버 응답의 detail도 보여 주지 않는다
  (FastAPI 검증 오류 detail에는 요청 원문이 섞여 돌아올 수 있다). 알려진 경우만 우리 문구로 바꾼다.
- 서버에 모델이 없으면(400 + LOCAL_PII_MODEL_DIR) 규칙만 모드로 다시 요청하고, 그 사실을 결과에 남긴다.
- 서버 작업(work_id)은 /process 시점부터 10분 동안만 남고, 복원은 한 번만 된다 (backend sessions.py).
- 파일 API는 오류도 FileResult(JSON)로 답한다. 보호본은 한 번만 내려받을 수 있다 (backend docs/file-api.md).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

GRADES = {"public_O": "O", "sensitive_S": "S", "confidential_C": "C"}
WORK_TTL_SECONDS = 600     # backend app/sessions.py DEFAULT_TTL_SECONDS
MAX_TEXT = 50_000          # backend ProcessIn.text max_length
MODEL_MISSING_HINT = "LOCAL_PII_MODEL_DIR"

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
UPLOAD_NAME = "upload.xlsx"         # 원래 파일 이름에도 개인정보가 있을 수 있어 보내지 않는다 (서버는 .xlsx만 확인)
FILE_TIMEOUT = 300.0                # 서버는 요청 안에서 칸마다 모델을 돌린다. 칸이 많으면 오래 걸린다
MAX_DOWNLOAD = 32 * 1024 * 1024     # 보호본은 값만 남긴 사본이라 올린 파일(최대 8MB)보다 크지 않다
BUSY_RETRIES = 5                    # 429 FILE_PROCESSING_BUSY: 서버는 파일을 한 번에 하나만 처리한다
HEALTH_RECHECK_SECONDS = 300        # 서버에 모델이 없다고 안 뒤로, 이만큼 지나면 /health로 다시 확인한다
_WORK_ID = re.compile(r"[0-9A-Za-z_-]{1,64}")

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
class Health:
    """/health. 탐지 범위는 backend#11 뒤로 알려 준다 (그 전 서버는 status만 준다)."""
    ok: bool
    detection: str | None = None   # model(모델+규칙) / rules_only(규칙만) / None(서버가 알려 주지 않음)
    slm: bool = False              # 기밀 탐지 SLM이 설정됨
    slm_ready: bool = False        # SLM 예열이 끝남 (배포 직후에는 잠깐 false)
    checked_at: float = field(default_factory=time.time)

    @property
    def model(self) -> bool | None:
        return None if self.detection is None else self.detection == "model"


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


@dataclass
class FileResult:
    """/files/sanitize 응답. 서버는 원래 값·파일 이름·매핑을 돌려주지 않는다 (유형별 개수도 없다)."""
    work_id: str
    status: str                  # READY / BLOCKED / FAILED
    grade: str | None = None     # O / S / C
    reason: str | None = None    # FAILED·BLOCKED일 때 (FILE_TOO_LARGE, MODEL_UNAVAILABLE, CONFIDENTIAL …)
    summary: dict = field(default_factory=dict)   # changed_cells, removed_comments/sheets/rows/columns
    http_status: int = 200


def is_insecure(url: str) -> bool:
    """암호화되지 않은 연결인가 (이 PC 안의 테스트 서버는 제외)."""
    parts = urlsplit(url)
    return parts.scheme != "https" and (parts.hostname or "").lower() not in ("localhost", "127.0.0.1", "::1")


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _detail(raw: bytes):
    data = _json(raw)
    return data.get("detail") if isinstance(data, dict) else None


def _multipart(field_name: str, filename: str, data: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = "kpguard-" + uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field_name}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n").encode("ascii")
    return head + data + f"\r\n--{boundary}--\r\n".encode("ascii"), f"multipart/form-data; boundary={boundary}"


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
        self.last_health: Health | None = None

    @property
    def insecure(self) -> bool:
        return is_insecure(self.base_url)

    def _open(self, method: str, path: str, data: bytes | None, headers: dict, timeout: float | None = None,
              limit: int = MAX_DOWNLOAD) -> tuple[int, object, bytes]:
        """→ (HTTP 상태, 헤더, 본문). 4xx·5xx도 본문과 함께 돌려준다. 연결하지 못하면 ServerError(unreachable)."""
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:  # noqa: S310 (설정한 서버 주소)
                raw = resp.read(limit + 1)
                if len(raw) > limit:
                    raise ServerError("서버 응답이 너무 커요.", "bad_response", resp.status)
                return resp.status, resp.headers, raw
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(64 * 1024)
            except Exception:
                raw = b""
            finally:
                exc.close()
            return exc.code, exc.headers, raw
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ServerError("서버에 연결하지 못했어요. 서버 주소와 네트워크를 확인하세요.", "unreachable") from None

    def _request(self, method: str, path: str, body=None, timeout: float | None = None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        status, _, raw = self._open(method, path, data, {"Content-Type": "application/json",
                                                         "Accept": "application/json"}, timeout)
        if status >= 400:
            raise _http_error(status, _detail(raw))
        out = _json(raw)
        if out is None:
            raise ServerError("서버 응답을 읽지 못했어요.", "bad_response")
        return out

    # ------------------------------------------------------------------ API
    def health(self) -> Health:
        """원문 없이 연결과 탐지 범위를 확인한다. 범위를 알려 주면 그에 맞춰 규칙만 모드를 정한다."""
        data = self._request("GET", "/health", timeout=min(self.timeout, 5.0))
        if not isinstance(data, dict):
            raise ServerError("서버 응답 형식이 달라요.", "bad_response")
        detection = data.get("detection") if data.get("detection") in ("model", "rules_only") else None
        health = Health(data.get("status") == "ok", detection, data.get("slm") is True, data.get("slm_ready") is True)
        self.last_health = health
        if health.ok and health.model is not None:
            self.model_available = health.model
        return health

    def process(self, text: str) -> ProcessResult:
        """서버에 모델이 없다고 알고 있으면 처음부터 규칙만 모드로 보낸다 (글을 두 번 보내지 않게)."""
        stale = self.last_health is None or time.time() - self.last_health.checked_at > HEALTH_RECHECK_SECONDS
        if self.model_available is False and stale:
            try:  # 그 사이 서버에 모델을 넣었을 수 있다
                self.health()
            except ServerError:
                pass
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

    # ------------------------------------------------------------------ 파일
    def sanitize_file(self, data: bytes, retries: int = BUSY_RETRIES, wait=time.sleep) -> FileResult:
        """xlsx 하나를 올려 검사한다. 서버가 다른 파일을 처리 중이면(429) Retry-After만큼 기다렸다 다시 보낸다.

        FAILED·BLOCKED도 FileResult로 돌려준다. 연결 실패·알 수 없는 응답만 ServerError로 알린다.
        """
        body, content_type = _multipart("file", UPLOAD_NAME, data, XLSX_MEDIA_TYPE)
        headers = {"Content-Type": content_type, "Accept": "application/json"}
        for attempt in range(retries + 1):
            status, resp_headers, raw = self._open("POST", "/files/sanitize", body, headers,
                                                   max(self.timeout, FILE_TIMEOUT), limit=64 * 1024)
            out = _json(raw)
            if not isinstance(out, dict) or out.get("status") not in ("READY", "BLOCKED", "FAILED"):
                if status >= 500:
                    raise ServerError(f"서버 오류가 났어요 (HTTP {status}).", "failed", status)
                if status == 404:
                    raise ServerError("서버에 파일 검사 기능이 없어요. 서버 버전을 확인하세요.", "rejected", status)
                raise ServerError("서버 응답을 읽지 못했어요.", "bad_response", status)
            if status == 429 and attempt < retries:
                try:
                    delay = float(resp_headers.get("Retry-After") or 1)
                except (TypeError, ValueError):
                    delay = 1.0
                wait(min(max(delay, 0.5), 5.0))
                continue
            summary = out.get("summary") if isinstance(out.get("summary"), dict) else {}
            try:
                return FileResult(str(out.get("work_id") or ""), out["status"], GRADES.get(out.get("grade")),
                                  out.get("reason") if isinstance(out.get("reason"), str) else None,
                                  {k: int(v) for k, v in summary.items() if isinstance(v, int)}, status)
            except (TypeError, ValueError):
                raise ServerError("서버 응답 형식이 달라요.", "bad_response", status) from None
        raise AssertionError("unreachable")  # pragma: no cover

    def download_file(self, work_id: str) -> bytes:
        """READY 보호본을 받는다. 서버는 한 번만 내주고 바로 지운다 (두 번째는 404 FILE_NOT_AVAILABLE)."""
        if not _WORK_ID.fullmatch(work_id or ""):
            raise ServerError("서버 응답 형식이 달라요.", "bad_response")
        status, headers, raw = self._open("GET", f"/files/{quote(work_id)}/download", None,
                                          {"Accept": XLSX_MEDIA_TYPE}, max(self.timeout, 60.0))
        if status == 404:
            raise ServerError("서버에서 보호본을 받지 못했어요. 이미 받았거나 10분이 지나 지워졌어요.", "expired", status)
        if status >= 400:
            raise _http_error(status, _detail(raw))
        ctype = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != XLSX_MEDIA_TYPE or not raw.startswith(b"PK"):
            raise ServerError("서버가 보낸 보호본이 xlsx 파일이 아니에요.", "bad_response", status)
        return raw
