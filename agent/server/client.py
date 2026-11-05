"""팀 서버(backend) API 클라이언트: /health, /auth/dev-token, /process, /process/decide, /process/restore,
/files/sanitize, /files/{work_id}/download.

탐지·가명화·기록·복원은 서버가 한다 (2026-11-05 결정). 이 PC는 화면과 트리거를 맡는다.

- 표준 라이브러리(urllib, ssl)만 쓴다. 회사 프록시 환경 변수(HTTPS_PROXY 등)는 urllib이 그대로 따른다.
- 오류 메시지에 원문과 토큰을 넣지 않는다. 서버 응답의 detail도 보여 주지 않는다
  (FastAPI 검증 오류 detail에는 요청 원문이 섞여 돌아올 수 있다). 알려진 경우만 우리 문구로 바꾼다.
- 인증 (backend#10): /health와 /auth/dev-token 말고는 `Authorization: Bearer <토큰>`이 필요하다.
  팀 토큰(관리자가 준 값, 서버 API_TOKENS) 또는 개발용 토큰(서버 POST /auth/dev-token이 바로 내주는 값)을 쓴다.
  개발용 토큰은 서버 메모리에만 있어 서버를 다시 시작하면(배포마다) 무효가 된다. 401이면 한 번 새로 받아 다시 보낸다.
  토큰은 HTTPS나 이 PC 안의 서버로만 보낸다 (insecure).
- HTTPS 인증서 고정: 팀 서버는 도메인 없이 자체 서명 인증서를 쓴다 (backend Caddyfile). 시스템이 믿는 인증서가
  아니므로 사용자가 확인한 인증서의 SHA-256 지문을 고정하고, 연결할 때마다 요청을 보내기 전에 서버 인증서가 그
  지문과 같은지 본다. 다르면 아무것도 보내지 않고 끊는다 (pin_mismatch). 고정하지 않으면 시스템 인증서 저장소로 확인한다.
- 서버에 모델이 없으면(400 + LOCAL_PII_MODEL_DIR) 규칙만 모드로 다시 요청하고, 그 사실을 결과에 남긴다.
- 서버 작업(work_id)은 /process 시점부터 10분 동안만 남고, 복원은 한 번만 된다 (backend sessions.py).
- 파일 API는 오류도 FileResult(JSON)로 답한다. 보호본은 한 번만 내려받을 수 있다 (backend docs/file-api.md).
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import http.client
import json
import re
import ssl
import threading
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

DEV_TOKEN_PATH = "/auth/dev-token"  # 공개: 바로 쓸 수 있는 개발용 토큰 (서버 DISABLE_DEV_TOKEN=1이면 403)
AUTH_CHECK_PATH = "/policy/current" # api 권한으로 읽기만 하는 경로. 원문 없이 토큰을 확인할 때 쓴다
PUBLIC_PATHS = frozenset({"/health", DEV_TOKEN_PATH})
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+=*")   # RFC 6750 b64token (헤더에 줄바꿈 등이 들어가지 않게)
_HEX64 = re.compile(r"[0-9a-f]{64}")

INSECURE_MESSAGE = "토큰과 글을 암호화되지 않은 연결(HTTP)로 보내지 않아요. 서버 주소를 https://로 바꾸세요."
PIN_MESSAGE = ("서버 인증서가 신뢰한 인증서와 달라요. 연결을 가로채는 중일 수 있어 아무것도 보내지 않았어요. "
               "서버 인증서를 바꿨는지 팀에 확인한 뒤 설정 창에서 다시 신뢰하세요.")
UNTRUSTED_MESSAGE = ("서버 인증서를 확인할 수 없어요 (자체 서명 인증서 등). 설정 창에서 인증서 지문을 "
                     "팀에서 받은 값과 맞춰 본 뒤 신뢰하세요.")
# 서버가 요청을 처리하지 않은 게 분명한 오류 (연결·인증 단계에서 멈춤) → 같은 작업으로 다시 시도해도 된다
NOT_PROCESSED = frozenset({"unreachable", "tls", "untrusted_cert", "pin_mismatch", "insecure",
                           "unauthorized", "forbidden"})
CERT_KINDS = frozenset({"untrusted_cert", "pin_mismatch"})
AUTH_KINDS = frozenset({"unauthorized", "forbidden", "insecure"})

# 앱의 사유 → 서버 사유 (서버에는 false_positive / business_need 두 가지뿐)
REASONS = {"false_positive": "false_positive", "business_need": "business_need",
           "approved": "business_need", "none": None, None: None}


class ServerError(Exception):
    """kind (화면이 안내를 고르는 기준):

    unreachable     서버에 닿지 않음 (주소·네트워크·프록시·시간 초과)
    tls             TLS 연결을 맺지 못함 (https가 아닌 포트 등)
    untrusted_cert  서버 인증서를 믿을 수 없음 (자체 서명 등). 지문을 확인하고 신뢰하면 된다
    pin_mismatch    서버 인증서가 신뢰한 지문과 다름. 가로채기일 수 있어 아무것도 보내지 않았다
                    (fingerprint = 지금 서버 인증서 지문)
    insecure        토큰을 암호화되지 않은 연결(HTTP)로 보내지 않음
    unauthorized    401: 토큰이 없거나 맞지 않음
    forbidden       403: 토큰 권한 부족, 또는 서버가 개발용 토큰을 내주지 않음
    no_model / expired / failed / rejected / bad_response
    """

    def __init__(self, message: str, kind: str = "failed", status: int | None = None,
                 fingerprint: str | None = None):
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.status = status
        self.fingerprint = fingerprint


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


def server_key(url: str) -> str:
    """신뢰한 인증서를 서버별로 저장할 때 쓰는 키: "호스트:포트" (기본 포트 포함)."""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def normalize_fingerprint(text) -> str | None:
    """SHA-256 인증서 지문 → 소문자 64자리 16진수. 형식이 아니면 None.

    openssl 출력("sha256 Fingerprint=AB:CD:…"), 콜론·공백으로 나눈 값, "SHA256:" 접두어를 받는다.
    """
    if not isinstance(text, str):
        return None
    value = text.strip().lower().split("=", 1)[-1].strip()
    value = re.sub(r"^sha-?256\s*(fingerprint)?\s*:?", "", value)
    value = re.sub(r"[\s:]", "", value)
    return value if _HEX64.fullmatch(value) else None


def format_fingerprint(fingerprint: str) -> str:
    """화면용: AB:CD:… (openssl, 브라우저와 같은 모양)."""
    value = normalize_fingerprint(fingerprint) or ""
    return ":".join(value[i:i + 2] for i in range(0, len(value), 2)).upper()


def valid_token(token) -> bool:
    return isinstance(token, str) and 0 < len(token) <= 1024 and _TOKEN.fullmatch(token) is not None


class CertificatePinError(Exception):
    """서버 인증서가 고정한 지문과 다르다. 요청은 보내지 않았다."""

    def __init__(self, fingerprint: str):
        super().__init__("server certificate does not match the pinned fingerprint")
        self.fingerprint = fingerprint


class _HTTPSConnection(http.client.HTTPSConnection):
    """TLS 연결을 맺은 직후, 요청을 보내기 전에 서버 인증서 지문을 확인한다 (pin이 있으면 같아야 한다)."""

    def __init__(self, *args, pin: str | None = None, seen: list | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._pin = pin
        self._seen = seen

    def connect(self):
        super().connect()  # 프록시(HTTPS_PROXY)를 쓰면 CONNECT 터널 안에서 서버와 직접 TLS를 맺는다
        der = self.sock.getpeercert(binary_form=True) or b""
        actual = hashlib.sha256(der).hexdigest()
        if self._seen is not None:
            self._seen.append(actual)
        if self._pin is not None and not hmac.compare_digest(actual, self._pin):
            self.close()
            raise CertificatePinError(actual)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, context: ssl.SSLContext, pin: str | None = None, seen: list | None = None):
        super().__init__(context=context)
        self._pin = pin
        self._seen = seen

    def https_open(self, req):
        return self.do_open(functools.partial(_HTTPSConnection, pin=self._pin, seen=self._seen), req,
                            context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리디렉션을 따라가지 않는다. urllib은 Authorization 헤더를 새 주소로 그대로 옮기므로
    (https → http, 다른 서버) 토큰이 샐 수 있다. API는 리디렉션을 쓰지 않는다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener(context: ssl.SSLContext, pin: str | None = None, seen: list | None = None):
    return urllib.request.build_opener(_NoRedirect, _HTTPSHandler(context, pin, seen))


def _tls_context(verify: bool) -> ssl.SSLContext:
    """verify=False는 지문을 고정했거나 지문만 읽을 때다. 그때는 _HTTPSConnection의 지문 확인이 신뢰의 근거다."""
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _connection_error(exc: BaseException) -> ServerError:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, CertificatePinError):
        return ServerError(PIN_MESSAGE, "pin_mismatch", fingerprint=reason.fingerprint)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ServerError(UNTRUSTED_MESSAGE, "untrusted_cert")
    if isinstance(reason, ssl.SSLError):
        return ServerError("서버와 암호화 연결(TLS)을 맺지 못했어요. 주소가 https인지, 포트가 맞는지 확인하세요.", "tls")
    return ServerError("서버에 연결하지 못했어요. 서버 주소와 네트워크를 확인하세요.", "unreachable")


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
    """token: 팀 토큰(있으면 이것만 쓴다). dev_token=True: 팀 토큰이 없을 때 서버에서 개발용 토큰을 받아 쓴다.
    pin: 신뢰한 서버 인증서의 SHA-256 지문 (https 주소에만 쓴다)."""

    def __init__(self, base_url: str, timeout: float = 20.0, *, token: str | None = None,
                 dev_token: bool = False, pin: str | None = None):
        url = (base_url or "").strip().rstrip("/")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("server url must look like https://host[:port]")
        token = (token or "").strip() or None
        if token is not None and not valid_token(token):
            raise ValueError("token must be a bearer token (letters, digits, -._~+/=)")
        fingerprint = normalize_fingerprint(pin) if pin else None
        if pin and fingerprint is None:
            raise ValueError("pin must be a SHA-256 certificate fingerprint")
        self.base_url = url
        self.timeout = timeout
        self.team_token = token is not None
        self.dev_token = dev_token and token is None
        self._token = token            # 개발용 토큰은 처음 필요할 때 받는다. "" = 토큰을 쓰지 않는 서버
        self._token_lock = threading.Lock()
        self.pin = fingerprint if parts.scheme == "https" else None
        self._opener = _opener(_tls_context(self.pin is None), self.pin)
        self.model_available: bool | None = None  # 모름 / 서버에 모델 있음 / 없음(규칙만)
        self.last_health: Health | None = None

    @property
    def insecure(self) -> bool:
        return is_insecure(self.base_url)

    @property
    def auth_mode(self) -> str:
        """team(팀 토큰) / dev(개발용 토큰) / none(토큰 없음)"""
        return "team" if self.team_token else ("dev" if self.dev_token else "none")

    @property
    def cert_mode(self) -> str | None:
        """pinned(지문 고정) / system(시스템 인증서 저장소) / None(HTTP)"""
        if urlsplit(self.base_url).scheme != "https":
            return None
        return "pinned" if self.pin else "system"

    def _open(self, method: str, path: str, data: bytes | None, headers: dict, timeout: float | None = None,
              limit: int = MAX_DOWNLOAD, opener=None) -> tuple[int, object, bytes]:
        """→ (HTTP 상태, 헤더, 본문). 4xx·5xx도 본문과 함께 돌려준다.

        연결·TLS·인증서 문제는 ServerError(unreachable / tls / untrusted_cert / pin_mismatch)."""
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with (opener or self._opener).open(req, timeout=timeout or self.timeout) as resp:
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
        except CertificatePinError as exc:
            raise ServerError(PIN_MESSAGE, "pin_mismatch", fingerprint=exc.fingerprint) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise _connection_error(exc) from None
        except http.client.HTTPException:
            raise ServerError("서버 응답을 읽지 못했어요.", "bad_response") from None

    # ------------------------------------------------------------------ 인증 (backend#10)
    def _token_now(self) -> str | None:
        if not self.dev_token:
            return self._token
        with self._token_lock:
            if self._token is None:
                self._token = self._issue_dev_token()
            return self._token or None

    def _drop_token(self, token: str | None) -> None:
        with self._token_lock:
            if (self._token or None) == token:
                self._token = None

    def _issue_dev_token(self) -> str:
        """POST /auth/dev-token → 토큰. 발급 경로가 없는 서버(인증 전 backend)는 ""(토큰 없이 보냄)."""
        if self.insecure:
            raise ServerError(INSECURE_MESSAGE, "insecure")
        status, _, raw = self._open("POST", DEV_TOKEN_PATH, json.dumps({"scope": "api"}).encode("utf-8"),
                                    {"Content-Type": "application/json", "Accept": "application/json"},
                                    min(self.timeout, 10.0), limit=4096)
        if status == 404:
            return ""
        if status == 403:
            raise ServerError("이 서버는 개발용 토큰을 내주지 않아요 (운영 서버). "
                              "관리자에게 팀 토큰을 받아 설정 창에 넣으세요.", "forbidden", status)
        if status >= 400:
            raise _http_error(status, None)
        data = _json(raw)
        token = data.get("token") if isinstance(data, dict) else None
        if not valid_token(token):
            raise ServerError("서버가 보낸 토큰 형식이 달라요.", "bad_response", status)
        return token

    def _auth_error(self, status: int, token: str | None) -> ServerError:
        if status == 403:
            return ServerError("이 토큰으로는 할 수 없는 요청이에요 (권한 부족). 관리자에게 확인하세요.", "forbidden", status)
        if token is None:
            message = "서버가 토큰을 요구해요. 설정 창에서 팀 토큰을 넣거나 개발용 토큰을 쓰세요."
        elif self.dev_token:
            message = "개발용 토큰을 새로 받았는데도 서버가 받지 않았어요. 잠시 뒤 다시 시도하세요."
        else:
            message = "서버가 팀 토큰을 받지 않았어요. 설정 창에서 토큰을 확인하세요 (만료됐거나 잘못 입력)."
        return ServerError(message, "unauthorized", status)

    def _send(self, method: str, path: str, data: bytes | None, headers: dict, timeout: float | None = None,
              limit: int = MAX_DOWNLOAD) -> tuple[int, object, bytes]:
        """토큰을 붙여 보낸다. 개발용 토큰이 401이면(서버를 다시 시작해 잊음) 한 번 새로 받아 다시 보낸다.
        401·403은 ServerError(unauthorized·forbidden)로 알린다."""
        public = path in PUBLIC_PATHS
        for attempt in range(2):
            sent = dict(headers)
            token = None if public else self._token_now()
            if token:
                if self.insecure:
                    raise ServerError(INSECURE_MESSAGE, "insecure")
                sent["Authorization"] = f"Bearer {token}"
            status, resp_headers, raw = self._open(method, path, data, sent, timeout, limit)
            if status == 401 and not public and self.dev_token and attempt == 0:
                self._drop_token(token)
                continue
            if status in (401, 403) and not public:
                raise self._auth_error(status, token)
            return status, resp_headers, raw
        raise AssertionError("unreachable")  # pragma: no cover

    def check_auth(self) -> str:
        """원문 없이 토큰을 확인한다 → "ok" / "unknown"(확인 경로가 없는 서버). 실패는 ServerError."""
        status, _, raw = self._send("GET", AUTH_CHECK_PATH, None, {"Accept": "application/json"},
                                    min(self.timeout, 10.0), limit=64 * 1024)
        if status == 404:
            return "unknown"
        if status >= 400:
            raise _http_error(status, _detail(raw))
        return "ok"

    def probe_certificate(self) -> str:
        """서버가 내보이는 인증서의 SHA-256 지문을 읽기만 한다 (믿고 쓰기 전에 사용자가 확인하는 용도).

        공개 /health만 요청하고 토큰·글은 보내지 않는다."""
        if urlsplit(self.base_url).scheme != "https":
            raise ServerError("https 주소만 인증서가 있어요.", "insecure")
        seen: list[str] = []
        opener = _opener(_tls_context(False), None, seen)
        try:
            self._open("GET", "/health", None, {"Accept": "application/json"}, min(self.timeout, 5.0),
                       64 * 1024, opener=opener)
        except ServerError:
            if not seen:
                raise
        return seen[-1]

    def _request(self, method: str, path: str, body=None, timeout: float | None = None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        status, _, raw = self._send(method, path, data, {"Content-Type": "application/json",
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

    def _detect_request(self, path: str, text: str):
        """서버에 모델이 없다고 알고 있으면 처음부터 규칙만 모드로 보낸다 (글을 두 번 보내지 않게)."""
        stale = self.last_health is None or time.time() - self.last_health.checked_at > HEALTH_RECHECK_SECONDS
        if self.model_available is False and stale:
            try:  # 그 사이 서버에 모델을 넣었을 수 있다
                self.health()
            except ServerError:
                pass
        rules_only = self.model_available is False
        try:
            data = self._request("POST", path, {"text": text, "rules_only": rules_only})
        except ServerError as exc:
            if exc.kind != "no_model" or rules_only:
                raise
            self.model_available = False
            rules_only = True
            data = self._request("POST", path, {"text": text, "rules_only": True})
        else:
            if not rules_only:
                self.model_available = True
        return data, rules_only

    def preview(self, text: str):
        """Exact ranges without retaining any work item on the team server."""
        data, rules_only = self._detect_request("/process/preview", text)
        spans = data.get("spans") if isinstance(data, dict) else None
        if not isinstance(spans, list) or not all(
                isinstance(s, dict) and type(s.get("start")) is int and type(s.get("end")) is int
                and 0 <= s["start"] < s["end"] <= len(text) and isinstance(s.get("type"), str)
                for s in spans):
            raise ServerError("서버의 탐지 위치를 읽지 못했습니다.", "bad_response")
        return spans, rules_only

    def process(self, text: str) -> ProcessResult:
        data, rules_only = self._detect_request("/process", text)
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
            status, resp_headers, raw = self._send("POST", "/files/sanitize", body, headers,
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
        status, headers, raw = self._send("GET", f"/files/{quote(work_id)}/download", None,
                                          {"Accept": XLSX_MEDIA_TYPE}, max(self.timeout, 60.0))
        if status == 404:
            raise ServerError("서버에서 보호본을 받지 못했어요. 이미 받았거나 10분이 지나 지워졌어요.", "expired", status)
        if status >= 400:
            raise _http_error(status, _detail(raw))
        ctype = (headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != XLSX_MEDIA_TYPE or not raw.startswith(b"PK"):
            raise ServerError("서버가 보낸 보호본이 xlsx 파일이 아니에요.", "bad_response", status)
        return raw
