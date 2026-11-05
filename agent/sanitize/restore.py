"""답변 복원 (§6): AI 답변 속 가짜 값을 원래 값으로 되돌린다.

- 긴 것부터 치환 ("김"을 먼저 바꾸면 "김민수"가 깨짐)
- 전화번호는 숫자만 비교 (구분자 무시)
- 이름만 부르는 짧은 변형은 호칭·조사 문맥이 있을 때만
- 복원 후 조사 보정
"""
from __future__ import annotations

import re

from .josa import fix_josa

_GIVEN_CTX = r"(?=\s?(?:님|씨|학생|이가|이는|이를|이에게|아(?![가-힣])|야(?![가-힣])|에게|한테))"


def _pattern(fake: str, kind: str) -> str:
    if kind == "phone":
        digits = re.sub(r"\D", "", fake)
        return r"(?<!\d)" + r"[-. ]?".join(re.escape(d) for d in digits) + r"(?!\d)"
    if kind == "given":
        return r"(?<![가-힣])" + re.escape(fake) + _GIVEN_CTX
    return re.escape(fake)


def restore(text: str, entries) -> tuple[str, list[tuple[int, int]]]:
    """복원된 텍스트와 복원된 구간(밑줄 표시용) 목록."""
    if not entries or not text:
        return text, []
    ordered = sorted(entries, key=lambda e: -len(e[0]))
    rx = re.compile("|".join(f"(?P<g{i}>{_pattern(f, k)})" for i, (f, _, k) in enumerate(ordered)))
    out: list[str] = []
    ranges: list[tuple[int, int]] = []
    last, length = 0, 0
    for m in rx.finditer(text):
        if m.start() < last:  # 조사 보정으로 이미 소비한 구간
            continue
        original = ordered[int(m.lastgroup[1:])][1]
        chunk = text[last:m.start()]
        out.append(chunk)
        length += len(chunk)
        out.append(original)
        ranges.append((length, length + len(original)))
        length += len(original)
        last = m.end()
        fixed, n = fix_josa(original, text, m.end())
        if n:
            out.append(fixed)
            length += len(fixed)
            last = m.end() + n
    out.append(text[last:])
    return "".join(out), ranges
