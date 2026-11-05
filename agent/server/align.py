"""서버 변환문과 원문 맞추기, 보낼 글 만들기.

서버(/process)는 가린 글(masked_text)만 돌려주고 탐지 위치는 주지 않는다. 가린 글은 원문의 탐지 구간을
[유형_번호] 자리표시로 바꾼 것이라, 자리표시 사이의 글자 조각을 원문에서 차례로 찾으면 원래 값을 되찾을 수 있다.

조각이 원문에 여러 번 나오면 맞추는 방법이 둘 이상일 수 있다. 앞에서부터 가장 이르게 맞춘 결과와
뒤에서부터 가장 늦게 맞춘 결과가 같은 항목만 위치가 '확정'된 것으로 보고, 나머지는 되돌리기를 막는다.
(가능한 모든 맞춤은 두 결과 사이에 있으므로, 둘이 같으면 맞춤이 하나뿐이다.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PLACEHOLDER = re.compile(r"\[([^\[\]\r\n]+)_(\d+)\]")  # backend local_ai/pseudonym.py와 같은 모양

# 서버 라벨 → 하이라이트 색 묶음 (detect/span.py CATEGORY와 같은 이름)
LABEL_CATEGORY = {
    "이름": "person", "전화번호": "contact", "이메일": "contact", "IP주소": "contact", "사용자명": "contact",
    "주소": "quasi", "생년월일": "quasi", "식별번호": "identifier",
    "계좌번호": "finance", "카드번호": "finance", "사업자번호": "finance",
    "비밀번호": "secret", "API키": "secret", "토큰": "secret",
}
# 서버 라벨 → 앱 유형 (이 PC의 로그는 앱 유형 이름으로 남긴다)
LABEL_TYPE = {
    "이름": "name", "전화번호": "phone", "이메일": "email", "주소": "address", "식별번호": "id_number",
    "계좌번호": "account", "카드번호": "card", "생년월일": "dob", "사용자명": "username", "IP주소": "ip",
    "비밀번호": "secret", "사업자번호": "bizno", "API키": "secret", "토큰": "secret",
}


@dataclass
class Repl:
    """서버로 보내기 전에 이 PC에서 태그로 바꾼 구간 (원문 위치 o_*, 보낸 글 위치 s_*)."""
    o_start: int
    o_end: int
    s_start: int
    s_end: int
    tag: str
    kind: str      # org / manual
    value: str


def premask(text: str, spans) -> tuple[str, list[Repl]]:
    """spans: [(시작, 끝, 태그, 종류, 값)]. 겹치면 앞(같으면 긴) 구간만 바꾼다. → (서버로 보낼 글, [Repl])"""
    out, repls, pos, delta = [], [], 0, 0
    for start, end, tag, kind, value in sorted(spans, key=lambda x: (x[0], x[0] - x[1])):
        if start < pos:
            continue
        out += [text[pos:start], tag]
        repls.append(Repl(start, end, start + delta, start + delta + len(tag), tag, kind, value))
        delta += len(tag) - (end - start)
        pos = end
    out.append(text[pos:])
    return "".join(out), repls


def to_orig(pos: int, repls: list[Repl]) -> int | None:
    """보낸 글의 위치 → 원문 위치. 태그 안쪽이면 None."""
    delta = 0
    for r in repls:
        if pos <= r.s_start:
            break
        if pos < r.s_end:
            return None
        delta += (r.o_end - r.o_start) - (r.s_end - r.s_start)
    return pos + delta


@dataclass
class Placed:
    token: str            # "[이름_1]"
    label: str            # "이름"
    start: int            # 원문 위치 (exact가 아니면 -1)
    end: int
    value: str | None     # 원래 값 (위치가 확정된 경우만)
    out_start: int        # 가린 글에서의 위치
    out_end: int
    exact: bool


def _literals(masked: str, toks) -> list[str]:
    """자리표시 앞뒤의 글자 조각: [앞, 사이1, 사이2, …, 뒤] (자리표시 수 + 1개)."""
    out, pos = [], 0
    for t in toks:
        out.append(masked[pos:t.start()])
        pos = t.end()
    out.append(masked[pos:])
    return out


def _rebuild(original: str, toks, spans) -> str:
    parts, pos = [], 0
    for t, (s, e) in zip(toks, spans):
        parts += [original[pos:s], t.group()]
        pos = e
    parts.append(original[pos:])
    return "".join(parts)


def _forward(original: str, masked: str, toks):
    lits = _literals(masked, toks)
    if not original.startswith(lits[0]):
        return None
    o, spans = len(lits[0]), []
    for i in range(len(toks)):
        lit = lits[i + 1]
        if i == len(toks) - 1:
            end = len(original) - len(lit)
            if end <= o or not original.endswith(lit):
                return None
        else:
            if not lit:  # 자리표시 둘이 붙어 있으면 경계를 알 수 없다
                return None
            end = original.find(lit, o + 1)
            if end < 0:
                return None
        spans.append((o, end))
        o = end + len(lit)
    return spans if _rebuild(original, toks, spans) == masked else None


def _backward(original: str, masked: str, toks):
    lits = _literals(masked, toks)
    n = len(toks)
    if not original.endswith(lits[n]):
        return None
    end, spans = len(original) - len(lits[n]), [None] * n
    for i in range(n - 1, -1, -1):
        lit = lits[i]
        if i == 0:
            start = len(lit)
            if start >= end or not original.startswith(lit):
                return None
        else:
            if not lit:
                return None
            p = original.rfind(lit, 0, end - 1)  # 값이 한 글자 이상 남도록
            if p < 0:
                return None
            start = p + len(lit)
        spans[i] = (start, end)
        end = start - len(lit)
    return spans if _rebuild(original, toks, spans) == masked else None


def align(original: str, masked: str) -> list[Placed]:
    """가린 글의 자리표시마다 원문 위치를 찾는다. 원문에 원래 있던 자리표시 모양 글자는 빼고 돌려준다."""
    toks = list(PLACEHOLDER.finditer(masked))
    if not toks:
        return []
    fwd = _forward(original, masked, toks)
    bwd = _backward(original, masked, toks)
    items = []
    for i, t in enumerate(toks):
        exact = fwd is not None and bwd is not None and fwd[i] == bwd[i]
        start, end = fwd[i] if exact else (-1, -1)
        value = original[start:end] if exact else None
        if exact and value == t.group():
            continue  # 사용자가 원래 써 둔 "[이름_1]" 같은 글자 (서버가 바꾼 것이 아님)
        items.append(Placed(t.group(), t.group(1), start, end, value, t.start(), t.end(), exact))
    return items


def compose(masked: str, items: list[Placed], reverted=frozenset(), extra=()) -> list[tuple[str, tuple | None]]:
    """보낼 글을 조각 [(글자, 표시)]으로 만든다.

    reverted: 원문 그대로 보낼 자리표시. extra: [(원래 값, 바꿀 글자, 종류)] — 서버가 가리지 않은 곳(자리표시 밖)에만
    적용한다 (조직 베이스 코드네임, 사용자가 신고한 놓친 정보). 표시: ("token", 자리표시) / ("reverted", 자리표시) /
    ("extra", 종류) / None(그대로).
    """
    values = {it.token: it.value for it in items if it.exact}
    known = {it.token for it in items}
    extra = sorted({(v, r, k) for v, r, k in extra if v}, key=lambda x: -len(x[0]))
    rx = re.compile("|".join(re.escape(v) for v, _, _ in extra)) if extra else None
    lookup = {v: (r, k) for v, r, k in extra}

    def literal(chunk: str):
        if not chunk:
            return []
        if rx is None:
            return [(chunk, None)]
        out, pos = [], 0
        for m in rx.finditer(chunk):
            if m.start() > pos:
                out.append((chunk[pos:m.start()], None))
            repl, kind = lookup[m.group()]
            out.append((repl, ("extra", kind)))
            pos = m.end()
        if pos < len(chunk):
            out.append((chunk[pos:], None))
        return out

    segs, pos = [], 0
    for m in PLACEHOLDER.finditer(masked):
        segs += literal(masked[pos:m.start()])
        tok = m.group()
        if tok in reverted and values.get(tok) is not None:
            segs.append((values[tok], ("reverted", tok)))
        elif tok in known:
            segs.append((tok, ("token", tok)))
        else:
            segs += literal(tok)
        pos = m.end()
    segs += literal(masked[pos:])
    return segs
