"""평문 변환 엔진 (§5).

처리 순서: 전략 결정 → 값 생성 → 치환(원문 좌표 기준, 위치 밀림 없음) → 조사 보정 → 삭제 자리 문장 정리.
매핑(가짜 값 → 원래 값)은 Session 메모리에만 있다. 디스크에 쓰지 않는다.
"""
from __future__ import annotations

import hmac
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date

from ..detect import lexicon, scoring
from ..detect.span import Span
from . import fakes
from .josa import fix_josa

STRATEGY_LABELS = {"replace": "대체", "generalize": "일반화", "delete": "삭제", "tag": "태그", "block": "차단"}

# 첫 번째가 기본 전략. 판단 기준: "AI가 이 작업을 하는 데 이 값이 필요한가?"
ALLOWED = {
    "rrn": ["delete", "tag"], "frn": ["delete", "tag"], "card": ["delete", "tag"],
    "bizno": ["delete", "tag"], "account": ["delete", "tag"],
    "phone": ["replace", "delete", "tag"], "email": ["replace", "delete", "tag"],
    "ip": ["replace", "tag", "delete"], "secret": ["tag", "delete"],
    "name": ["replace", "tag", "delete"], "age": ["generalize", "delete"],
    "address": ["delete", "generalize", "tag"], "school": ["replace", "generalize", "tag", "delete"],
    "grade_class": ["generalize", "delete"], "codename": ["tag", "delete"],
    "client": ["replace", "tag"], "internal_host": ["tag", "delete"],
    "classified": ["block", "delete"], "source_code": ["block", "delete"],
    "other": ["tag", "delete"],
    "dob": ["generalize", "delete", "tag"], "id_number": ["delete", "tag"], "username": ["tag", "delete"],
}

_FIXED_TAGS = {"rrn": "[주민번호]", "frn": "[외국인번호]", "card": "[카드번호]", "bizno": "[사업자번호]",
               "account": "[계좌번호]", "phone": "[전화번호]", "email": "[이메일]", "ip": "[IP]",
               "secret": "[비밀값]", "address": "[주소]", "school": "[학교]", "internal_host": "[사내도메인]",
               "other": "[가림]", "dob": "[생년월일]", "id_number": "[신분증번호]", "username": "[계정]"}
FIXED_TAG_VALUES = frozenset(_FIXED_TAGS.values())
# 태그 중 복원 가능한 것 (같은 값 → 같은 태그, A/B/C 구분)
_COUNTER_TAGS = {"name": "이름", "codename": "프로젝트", "client": "고객사"}

# 삭제 후 남으면 어색한 꼬리
_ADDR_TAIL = re.compile(r"(?:에\s?(?:사는|살고 있는|거주하는|거주 중인)|에서\s?(?:온|사는))\s*")
_JOSA_TAIL = re.compile(
    r"(?:\s?(?:이에요|이예요|입니다|이고|이며|이야|이다)|이랑|이나|으로|로|은|는|이|가|을|를|과|와|도|의)(?![가-힣])"
)


# ====================================================================== 세션 (매핑 저장소)
class Session:
    """같은 원문 → 같은 가짜 값. HMAC(세션키, 원문)을 시드로 풀에서 고른다.

    점검 창(메인 스레드)과 파일 검사(작업 스레드)가 같은 매핑을 쓰므로 값 발급은 잠금 안에서 한다.
    """

    def __init__(self, base: dict):
        self.base = base
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._key = os.urandom(32)
            self._fwd: dict[tuple, str] = {}
            self._used: set[str] = set()
            self._counters: dict[str, int] = {}
            self._seq = 0
            self.entries: list[tuple[str, str, str]] = []  # (가짜 값, 원래 값, 종류 plain/phone/given)
            self.created = time.time()

    def known_values(self) -> list[tuple[str, str]]:
        """이 세션에서 가린 (유형, 원래 값). 파일 검사에서 다른 칸에 같은 값이 있으면 함께 가린다."""
        with self._lock:
            return [(typ, value) for (_, typ, value) in self._fwd]

    def fakes(self) -> set[str]:
        """이 세션이 만든 가짜 값과 태그 (재검사에서 우리가 넣은 값은 민감정보로 보지 않는다)."""
        with self._lock:
            return set(self._fwd.values()) | FIXED_TAG_VALUES

    def _seed(self, typ: str, value: str) -> int:
        digest = hmac.new(self._key, f"{typ}:{value}".encode(), "sha256").digest()
        return int.from_bytes(digest[:8], "big")

    def _remember(self, fake: str, original: str, kind: str = "plain") -> None:
        if (fake, original, kind) not in self.entries:
            self.entries.append((fake, original, kind))

    def fake_for(self, typ: str, value: str, text: str) -> str:
        with self._lock:
            return self._fake_for(typ, value, text)

    def tag_for(self, typ: str, value: str) -> str:
        with self._lock:
            return self._tag_for(typ, value)

    def _fake_for(self, typ: str, value: str, text: str) -> str:
        key = ("replace", typ, value)
        if key in self._fwd:
            return self._fwd[key]
        seed = self._seed(typ, value)
        taken = lambda c: c in self._used or c in text  # 원문에 이미 있는 값은 쓰지 않는다
        if typ == "name":
            fake = fakes.fake_name(value, seed, taken)
        elif typ == "phone":
            self._seq += 1
            fake = fakes.fake_phone(value, self._seq)
        elif typ == "email":
            self._seq += 1
            fake = fakes.fake_email(self._seq)
        elif typ == "ip":
            self._seq += 1
            fake = fakes.fake_ip(self._seq)
        elif typ == "school":
            fake = fakes.pick(fakes.SCHOOLS, seed, taken)
        elif typ == "client":
            fake = fakes.pick(fakes.CLIENTS, seed, taken)
        else:
            return self._tag_for(typ, value)
        self._fwd[key] = fake
        self._used.add(fake)
        self._remember(fake, value, "phone" if typ == "phone" else "plain")
        if typ == "name" and len(fake) == 3 and len(value) == 3:
            self._remember(fake[1:], value[1:], "given")  # "서윤아", "서윤 씨" 같은 이름만 부르기
        return fake

    def _tag_for(self, typ: str, value: str) -> str:
        if typ not in _COUNTER_TAGS:
            return _FIXED_TAGS.get(typ, "[가림]")
        key = ("tag", typ, value)
        if key not in self._fwd:
            n = self._counters.get(typ, 0)
            self._counters[typ] = n + 1
            tag = f"[{_COUNTER_TAGS[typ]}{chr(ord('A') + n % 26)}]"
            self._fwd[key] = tag
            self._remember(tag, value)
        return self._fwd[key]


# ====================================================================== 계획·결과
@dataclass
class Item:
    span: Span
    strategy: str
    applied: bool
    revert_reason: str | None = None
    replacement: str = ""
    out_start: int = -1
    out_end: int = -1

    @property
    def is_blocker(self) -> bool:
        return self.span.grade == "C"

    @property
    def suspect(self) -> bool:
        """자동 변환 기준 미만 + 사용자가 아직 손대지 않음 → 하이라이트만."""
        return not self.applied and self.revert_reason is None


@dataclass
class Result:
    text: str
    items: list[Item]
    mapping: dict[str, str] = field(default_factory=dict)


def make_items(det, decisions: dict) -> list[Item]:
    items = []
    for s in det.blockers + det.spans:
        d = decisions.get(s.key, {})
        strategy = d.get("strategy") or ALLOWED[s.type][0]
        applied = d.get("applied", s.grade == "C" or s.confidence >= scoring.AUTO_APPLY)
        items.append(Item(s, strategy, applied, d.get("reason")))
    items.sort(key=lambda it: (it.span.start, -it.span.length))
    return items


def generalize(typ: str, value: str, base: dict) -> str:
    if typ == "age":
        n = int(re.match(r"\d+", value).group())
        if n < 8:
            return "미취학 아동"
        if n <= 13:
            return "초등학생"
        if n <= 16:
            return "중학생"
        if n <= 19:
            return "고등학생"
        return f"{n // 10 * 10}대"
    if typ == "address":
        return lexicon.address_region(value) or "[지역]"
    if typ == "grade_class":
        m = re.match(r"(\d{1,2})\s?학년", value)
        return f"{m.group(1)}학년" if m else "[학년]"
    if typ == "dob":
        return _decade_of_birth(value)
    if typ == "school":
        kind = base.get("schools", {}).get(value)
        if kind:
            return kind
        for suffix, word in (("초등학교", "초등학교"), ("중학교", "중학교"), ("여중", "중학교"),
                             ("대학교", "대학교")):
            if value.endswith(suffix):
                return word
        return "고등학교"
    return "[가림]"


def _decade_of_birth(value: str) -> str:
    """생년월일 → 'OO년대생'. 연도를 못 읽으면 태그로."""
    m = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", value)
    if m:
        year = int(m.group(1))
    else:  # "95년 3월", "950315", "95.03.15"
        m = re.match(r"\s*(\d{2})(?:\d{4}(?!\d)|\s*[년./-])", value)
        if not m:
            return "[생년월일]"
        yy = int(m.group(1))
        year = 2000 + yy if 2000 + yy <= date.today().year else 1900 + yy
    return f"{year // 10 * 10}년대생"


def _replacement(it: Item, session: Session, text: str) -> str:
    s = it.span
    if it.strategy == "replace":
        return session.fake_for(s.type, s.value, text)
    if it.strategy == "tag":
        return session.tag_for(s.type, s.value)
    if it.strategy == "generalize":
        return generalize(s.type, s.value, session.base)
    return ""


def _extend_delete(text: str, span: Span) -> int:
    end = span.end
    if span.type == "address":
        m = _ADDR_TAIL.match(text, end)
        if m:
            return m.end()
    m = _JOSA_TAIL.match(text, end)
    return m.end() if m else end


def replacement_for(it: Item, session: Session, text: str) -> str:
    """항목 하나를 가렸을 때 넣을 값 (지우기면 ""). 칩에서 한 항목만 입력창에 바로 바꿀 때 쓴다."""
    return "" if it.strategy == "delete" else _replacement(it, session, text)


def delete_end(text: str, span: Span) -> int:
    """지우기로 함께 없앨 끝 위치 (뒤에 붙은 조사·'에 사는' 같은 꼬리 포함)."""
    return _extend_delete(text, span)


# ====================================================================== 문장 정리
_CLEAN_RULES = [re.compile(p, re.M) for p in (
    r"(?P<rm>\(\s*[,;·]?\s*\))",          # 빈 괄호
    r"\((?P<rm>\s*[,;]\s*)",              # "( , 값" → "(값"
    r"(?P<rm>\s*[,;]\s*)\)",              # "값, )" → "값)"
    r"(?P<rm>[,;]\s*)(?=[,;])",           # 연속 쉼표
    r"^(?P<rm>\s*[,;]\s*)",               # 줄 맨 앞 쉼표
    r"[ \t](?P<rm>[ \t]+)",               # 연속 공백
    r"^(?P<rm>[ \t]+)",                   # 삭제로 생긴 줄 앞 공백
    r"(?P<rm>[ \t]+)(?=[,.!?)\n]|$)",     # 문장부호 앞 공백
)]


def _cleanup(cells: list[list]) -> list[list]:
    """삭제 지점 근처에서만 정리한다 (코드 들여쓰기 같은 다른 공백은 건드리지 않음).
    cells: [문자, 소유자]. 소유자 None = 원문 문자(지울 수 있음), ("del", i) = 삭제 지점 표식."""
    for _ in range(5):
        visible = [i for i, c in enumerate(cells) if c[0] != ""]
        s = "".join(cells[i][0] for i in visible)
        dels, k = [], 0
        for c in cells:
            if c[0] == "":
                dels.append(k)
            else:
                k += 1
        if not dels:
            return cells
        remove = set()
        for rx in _CLEAN_RULES:
            for m in rx.finditer(s):
                a, b = m.span()
                if not any(a <= p <= b for p in dels):
                    continue
                for j in range(*m.span("rm")):
                    if cells[visible[j]][1] is None:
                        remove.add(visible[j])
        if not remove:
            break
        cells = [c for i, c in enumerate(cells) if i not in remove]
    return cells


# ====================================================================== 변환
def sanitize(text: str, items: list[Item], session: Session) -> Result:
    for it in items:
        it.out_start = it.out_end = -1
        it.replacement = ""

    # '빼고 보내기'로 지운 기밀 블록 안의 항목은 처리할 필요가 없다
    cut_blocks = [it.span for it in items if it.is_blocker and it.applied and it.strategy == "delete"]
    active = []
    for idx, it in enumerate(items):
        if it.is_blocker and not (it.applied and it.strategy == "delete"):
            continue
        if not it.is_blocker and any(it.span.overlaps(b) for b in cut_blocks):
            continue
        active.append(idx)

    cells: list[list] = []

    def emit(chunk: str, owner) -> None:
        cells.extend([ch, owner] for ch in chunk)

    cursor = 0
    for idx in active:
        it = items[idx]
        s, e = it.span.start, it.span.end
        if s < cursor:
            continue
        emit(text[cursor:s], None)
        if not it.applied:
            emit(text[s:e], ("keep", idx))
            cursor = e
            continue
        if it.strategy == "delete":
            cells.append(["", ("del", idx)])
            cursor = e if it.is_blocker else _extend_delete(text, it.span)
            continue
        repl = _replacement(it, session, text)
        it.replacement = repl
        emit(repl, ("rep", idx))
        cursor = e
        fixed, n = fix_josa(repl, text, e)
        if n:
            emit(fixed, None)
            cursor = e + n
    emit(text[cursor:], None)

    cells = _cleanup(cells)
    pos = 0
    for ch, owner in cells:
        if owner and owner[0] in ("rep", "keep") and ch:
            it = items[owner[1]]
            if it.out_start < 0:
                it.out_start = pos
            it.out_end = pos + 1
        pos += len(ch)

    out = "".join(c[0] for c in cells)
    mapping = {fake: orig for fake, orig, _ in session.entries}
    return Result(out, items, mapping)
