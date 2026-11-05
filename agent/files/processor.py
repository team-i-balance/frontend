"""파일 검사 (§8, R3): xlsx 사본 변환을 앱의 탐지·변환 엔진에 연결한다.

- 표 처리(숨긴 시트·행·열, 메모, 문서 속성, 숫자 서식 검사 → 보이는 값만 새 통합 문서에 쓰기 → 보호본 재검사)는
  backend file_sanitizer를 그대로 쓴다 (`agent/files/xlsx`).
- 칸마다 무엇을 가릴지는 점검 창과 같은 엔진(detect → make_items → sanitize)이 정한다.
  같은 Session을 쓰므로 프롬프트와 파일에서 같은 값은 같은 가짜 값이 되고, AI 답변도 복원된다.
- 파일 내용은 이 PC의 메모리에서만 다룬다. 로그에는 유형·건수만 남긴다 (호출하는 쪽에서).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..detect import lexicon, rules
from ..detect.pipeline import Detection, KnownValues, detect
from ..detect.scoring import AUTO_APPLY
from ..detect.span import Span
from ..sanitize.engine import make_items, sanitize

MAX_FILE_BYTES = 8 * 1024 * 1024  # file_sanitizer와 같은 한도. 큰 파일은 읽기 전에 거른다

REASON_MESSAGES = {
    "CONFIDENTIAL": "기밀로 판정된 내용이 있어 외부 AI 전송을 막았어요. 사내 AI를 이용하세요.",
    "FILE_TOO_LARGE": "파일이 너무 커요. 압축 8MB, 풀었을 때 24MB, 전체 10만 칸까지 검사할 수 있어요.",
    "UNSUPPORTED_FEATURE": "수식·매크로·차트·그림·병합 셀·필터·표 서식 등은 아직 지원하지 않아요. "
                           "값만 남긴 표로 저장해서 다시 검사하세요.",
    "INVALID_XLSX": "xlsx 파일 구조를 읽을 수 없어요. 암호가 걸렸거나 손상된 파일일 수 있어요.",
    "UNSUPPORTED_FILE_TYPE": "지금은 엑셀(.xlsx) 파일만 검사할 수 있어요.",
    "INVALID_INPUT": "파일을 읽지 못했어요.",
    "READ_ERROR": "파일을 열 수 없어요. 다른 프로그램에서 열려 있는지 확인하세요.",
    "RESIDUAL_CHECK_FAILED": "변환한 사본을 다시 검사했더니 민감정보가 남아 있어 보호본을 만들지 않았어요.",
    "INVALID_PROCESSOR_RESULT": "검사 결과가 올바르지 않아 보호본을 만들지 않았어요.",
    "PROCESSOR_ERROR": "검사 중 오류가 나서 보호본을 만들지 않았어요.",
    "INVALID_COPY": "보호본을 검증하지 못해 만들지 않았어요.",
    "COPY_VERIFICATION_ERROR": "보호본을 검증하지 못해 만들지 않았어요.",
    "MODEL_FAILED": "소형 모델 검사에 실패해 보호본을 만들지 않았어요. 설정에서 모델을 끄면 규칙·사전으로만 검사해요.",
    "DEPENDENCY_MISSING": "파일 검사에 필요한 모듈이 설치돼 있지 않아요 (openpyxl, defusedxml).",
    # 서버 처리 모드 (backend /files/sanitize, /files/{work_id}/download)
    "SERVER_CONFIDENTIAL": "서버 검사에서 비밀값(API 키·토큰·비밀번호)이나 기밀 내용이 나와 보호본을 만들지 않았어요. "
                           "사내 AI를 이용하세요.",
    "SERVER_UNSET": "서버 주소가 설정돼 있지 않아요. 설정 창에서 지정하거나 이 PC에서 검사하세요.",
    "SERVER_UNREACHABLE": "서버에 연결하지 못했어요. 서버 주소와 네트워크를 확인하세요.",
    "MODEL_UNAVAILABLE": "서버에 탐지 모델이 없어 파일을 검사할 수 없어요 (서버 파일 검사는 모델이 있어야 해요).",
    "SERVER_RULES_ONLY": "서버에 탐지 모델이 없어(규칙만) 파일을 서버로 보내지 않았어요. 이 PC에서 검사하세요.",
    "FILE_PROCESSING_BUSY": "서버가 다른 파일을 처리하고 있어요. 잠시 후 다시 시도하세요.",
    "FILE_STORE_FULL": "서버 보관 공간이 가득 찼어요. 10분쯤 지나 다시 시도하세요.",
    "FILE_NOT_AVAILABLE": "서버에서 보호본을 받지 못했어요. 이미 받았거나 10분이 지나 지워졌어요. 다시 검사하세요.",
    "PROCESSING_ERROR": "서버에서 검사 중 오류가 나서 보호본을 만들지 않았어요.",
    "PROCESSING_CANCELLED": "서버 검사가 중간에 끊겼어요. 다시 시도하세요.",
    "SERVER_REJECTED": "서버가 파일 요청을 받지 않았어요. 서버 버전을 확인하세요.",
    "SERVER_ERROR": "서버에서 검사하지 못했어요.",
    "SERVER_COPY_INVALID": "서버가 보낸 보호본이 올린 사본과 맞지 않아 쓰지 않았어요. 다시 검사하세요.",
    # 연결 보안·인증 (backend#10). 파일은 서버로 보내지 않았다
    "SERVER_TLS_ERROR": "서버와 암호화 연결(TLS)을 맺지 못했어요. 서버 주소가 https인지, 포트가 맞는지 확인하세요.",
    "SERVER_CERT_UNTRUSTED": "서버 인증서를 확인할 수 없어 파일을 보내지 않았어요. "
                             "설정 창의 '인증·인증서'에서 지문을 확인하고 신뢰하세요.",
    "SERVER_CERT_MISMATCH": "서버 인증서가 신뢰한 인증서와 달라 파일을 보내지 않았어요. "
                            "연결을 가로채는 중일 수 있어요. 팀에 확인하세요.",
    "SERVER_INSECURE": "암호화되지 않은 연결(HTTP)로는 토큰과 파일을 보내지 않아요. 서버 주소를 https://로 바꾸세요.",
    "SERVER_UNAUTHORIZED": "서버가 토큰을 받지 않아 파일을 검사하지 못했어요. 설정 창의 '인증·인증서'에서 토큰을 확인하세요.",
    "SERVER_FORBIDDEN": "이 토큰으로는 서버 파일 검사를 쓸 수 없어요 (권한 부족, 또는 개발용 토큰을 막은 서버).",
}
# 서버 쪽 문제라 이 PC에서 검사하면 보호본을 만들 수 있는 경우
FALLBACK_REASONS = frozenset({"SERVER_UNSET", "SERVER_UNREACHABLE", "MODEL_UNAVAILABLE", "SERVER_RULES_ONLY",
                              "FILE_PROCESSING_BUSY",
                              "FILE_STORE_FULL", "FILE_NOT_AVAILABLE", "PROCESSING_ERROR", "PROCESSING_CANCELLED",
                              "SERVER_REJECTED", "SERVER_ERROR", "SERVER_COPY_INVALID",
                              "SERVER_TLS_ERROR", "SERVER_CERT_UNTRUSTED", "SERVER_CERT_MISMATCH",
                              "SERVER_INSECURE", "SERVER_UNAUTHORIZED", "SERVER_FORBIDDEN"})

# 재검사에서 우리가 넣은 값 뒤에 붙은 조사만 남으면 남은 민감정보로 보지 않는다
_JOSA = {"이", "가", "은", "는", "을", "를", "의", "도", "만", "에", "에게", "한테", "께", "께서", "와", "과",
         "랑", "이랑", "로", "으로", "아", "야", "님", "씨", "에서", "까지", "부터", "처럼", "보다", "하고", "나", "이나"}
_WORDISH = re.compile(r"[0-9A-Za-z가-힣]")
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def available() -> bool:
    try:
        import defusedxml  # noqa: F401
        import openpyxl  # noqa: F401
    except ImportError:
        return False
    return True


class _MemoModel:
    """파일 하나를 처리하는 동안 같은 글을 두 번 추론하지 않게 결과를 기억한다 (처리가 끝나면 버린다)."""

    def __init__(self, model):
        self.model = model
        self.memo: dict[str, list[Span]] = {}

    def detect(self, text: str) -> list[Span]:
        if text not in self.memo:
            self.memo[text] = self.model.detect(text)  # ModelError는 pipeline이 받아 coverage로 알린다
        return [Span(**vars(s)) for s in self.memo[text]]


class EngineProcessor:
    """file_sanitizer.Processor 구현: 앱의 엔진으로 칸·메모·문서 속성을 검사하고 변환한다."""

    def __init__(self, base: dict, session, *, ignore=frozenset(), model=None):
        self.base = base
        self.session = session
        self.ignore = frozenset(ignore)
        self.model = _MemoModel(model) if model is not None else None
        self.found: Counter = Counter()          # 보호본에 들어갈 칸에서 가린 값 (유형별)
        self.hidden_found: Counter = Counter()   # 메모·숨긴 시트·문서 속성 등에서 찾은 값 → 보호본에서 빠짐
        self.suspects: Counter = Counter()       # 확신이 낮아 바꾸지 않은 값
        self.blocker_types: Counter = Counter()
        self.blocker_reasons: list[str] = []
        self.coverage = "model" if model is not None else "rules"
        self.model_failed = False
        self.outputs: set[str] = set()     # 우리가 넣은 값 (재검사에서 제외)
        self.originals: set[str] = set()   # 가린 원래 값 (보호본에 남으면 실패)

    def _detect(self, text: str, known=None):
        det = detect(text, self.base, self.ignore, model=self.model, known=known, model_max_chars=None)
        if det.coverage == "model_failed":
            self.model_failed = True
            self.coverage = "model_failed"
        return det

    def _items(self, det):
        return make_items(det, {})

    # ------------------------------------------------------------------ Processor
    def process(self, session_id, segments):
        from .xlsx.sanitizer import Decision

        texts = list(dict.fromkeys(s.text for s in segments))
        # 1차: 칸마다 탐지하고, 확실한 값을 모은다
        first = {t: self._detect(t) for t in texts}
        known: dict[tuple[str, str], float] = {}
        for det in first.values():
            for s in det.spans:
                if s.confidence >= AUTO_APPLY:
                    known.setdefault((s.type, s.value), s.confidence)
        for typ, value in self.session.known_values():  # 이 세션에서 이미 가린 값
            known.setdefault((typ, value), AUTO_APPLY)
        values = KnownValues([(t, v, c) for (t, v), c in known.items()])
        # 2차: 다른 칸에서 확인된 값은 문맥이 없어도 함께 가린다 ("홍길동 학생" 칸과 "홍길동" 칸)
        results = {}
        for t in texts:
            det = self._detect(t, values) if values.info else first[t]
            items = self._items(det)
            results[t] = (items, sanitize(t, items, self.session))

        decisions = {}
        for seg in segments:
            items, result = results[seg.text]
            blockers = [it for it in items if it.is_blocker and it.applied and it.strategy == "block"]
            if blockers:
                self.blocker_types.update(it.span.type for it in blockers)
                self.blocker_reasons += [it.span.reason for it in blockers]
                decisions[seg.id] = Decision("C")
                continue
            applied = [it for it in items if it.applied and not it.is_blocker]
            self.suspects.update(it.span.type for it in items if not it.is_blocker and it.suspect)
            if not applied:
                decisions[seg.id] = Decision("O", seg.text if seg.exportable else None)
                continue
            (self.found if seg.exportable else self.hidden_found).update(it.span.type for it in applied)
            for it in applied:
                self.originals.add(it.span.value)
                if it.replacement:
                    self.outputs.add(it.replacement)
            decisions[seg.id] = Decision("S", (result.text or None) if seg.exportable else None)
        return decisions

    def verify(self, session_id, segments) -> bool:
        """보호본을 다시 읽어 검사한다. 가린 원래 값이나 새 민감정보가 남았으면 False. 매핑은 늘리지 않는다."""
        ours = sorted(self.session.fakes() | self.outputs, key=len, reverse=True)
        originals = [o for o in self.originals if len(o) >= 2]
        for text in dict.fromkeys(s.text for s in segments):
            if any(o in text for o in originals):
                return False
            det = self._detect(text)
            if det.blockers:
                return False
            for s in det.spans:
                if s.confidence < AUTO_APPLY:
                    continue
                rest = s.value
                for o in ours:
                    rest = rest.replace(o, " ")
                rest = rest.strip()
                if rest and rest not in _JOSA and _WORDISH.search(rest):
                    return False
        return True


class OrgProcessor(EngineProcessor):
    """서버 처리 모드의 첫 단계 (이 PC): 서버에 없는 것만 한다.

    - 조직 베이스(코드네임·고객사·사내 도메인)는 점검 창과 같은 태그([프로젝트A], [고객사A], [사내도메인])로 바꾼다.
      같은 Session을 쓰므로 AI 답변의 태그는 이 PC의 '답변 복원'에서 되돌릴 수 있다.
    - 기밀 표기·사내 소스코드가 있으면 막는다 → 파일이 서버로 가지 않는다.
    개인정보 탐지·가명화는 서버가 한다.
    """

    def __init__(self, base: dict, session, *, ignore=frozenset()):
        super().__init__(base, session, ignore=ignore, model=None)

    def _detect(self, text: str, known=None):
        blockers = [b for b in lexicon.detect_blockers(text, self.base, rules.code_ranges(text))
                    if b.value not in self.ignore]
        return Detection(lexicon.org_spans(text, self.base, self.ignore), blockers)

    def _items(self, det):
        return make_items(det, {s.key: {"strategy": "tag"} for s in det.spans})


# ====================================================================== 앱에서 부르는 함수
@dataclass
class FileReport:
    status: str                       # READY / BLOCKED / FAILED
    reason: str | None = None
    grade: str | None = None          # READY일 때 S(가린 값 있음) / O
    file_bytes: bytes | None = None   # READY일 때만
    summary: object | None = None     # file_sanitizer Summary (변환 칸, 제거한 메모·숨긴 시트·행·열)
    found: Counter = field(default_factory=Counter)
    hidden_found: Counter = field(default_factory=Counter)
    suspects: Counter = field(default_factory=Counter)
    blocker_reasons: list[str] = field(default_factory=list)
    blocker_types: Counter = field(default_factory=Counter)
    coverage: str = "rules"
    suggested_name: str = "보호본.xlsx"
    # 서버 처리 모드
    where: str = "local"                      # local / server
    org_found: Counter = field(default_factory=Counter)   # 서버로 보내기 전에 이 PC에서 가린 조직 베이스
    server_tokens: frozenset = frozenset()    # 서버가 넣은 자리표시 ([이름_1] 등). 값은 없다
    fallback: bool = False                    # 서버 쪽 문제 → '이 PC에서 검사'를 권한다

    @property
    def message(self) -> str:
        return REASON_MESSAGES.get(self.reason or "", "")


def suggested_name(filename: str, base: dict, session, ignore=frozenset()) -> str:
    """보호본 파일 이름. 원래 이름에 개인정보가 있으면 이름도 바꾼다 ("홍길동_상담.xlsx" → "빈가람_상담_보호본.xlsx")."""
    stem = Path(filename).stem
    known = [(typ, value, AUTO_APPLY) for typ, value in session.known_values()]  # 파일 안에서 가린 값
    det = detect(stem, base, ignore, known=known)
    items = make_items(det, {})
    if any(it.is_blocker for it in items):
        stem = ""
    else:
        stem = sanitize(stem, items, session).text
    stem = _BAD_FILENAME.sub("_", stem).strip(" ._")
    return f"{stem}_보호본.xlsx" if stem else "보호본.xlsx"


def inspect_xlsx(path, base: dict, session, *, ignore=frozenset(), model=None) -> FileReport:
    """xlsx 하나를 검사해 보호본을 만든다. 원본 파일은 바꾸지 않는다."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return FileReport("FAILED", "UNSUPPORTED_FILE_TYPE")
    if not available():
        return FileReport("FAILED", "DEPENDENCY_MISSING")
    from .xlsx.sanitizer import sanitize_xlsx

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return FileReport("FAILED", "FILE_TOO_LARGE")
        data = path.read_bytes()
    except OSError:
        return FileReport("FAILED", "READ_ERROR")
    processor = EngineProcessor(base, session, ignore=ignore, model=model)
    result = sanitize_xlsx(data, f"kpguard-{session.created:.0f}", processor, filename=path.name)
    report = FileReport(result.status, result.reason, result.grade, result.file_bytes, result.summary,
                        processor.found, processor.hidden_found, processor.suspects,
                        list(dict.fromkeys(processor.blocker_reasons)), processor.blocker_types, processor.coverage)
    if processor.model_failed and report.status == "READY":
        # 모델로 검사하지 못한 칸이 있으면 완전한 검사가 아니므로 보호본을 내놓지 않는다
        report.status, report.reason, report.file_bytes, report.summary = "FAILED", "MODEL_FAILED", None, None
    if report.status == "READY":
        report.suggested_name = suggested_name(path.name, base, session, ignore)
    return report
