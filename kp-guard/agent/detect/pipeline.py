"""탐지 파이프라인: 규칙 → 사전·베이스 → (Phase 4: 애매한 구간만 소형 모델) → 겹침 정리 → 조합 위험."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import lexicon, rules, scoring
from .span import Span

_PROPAGATE = {"name", "client", "school"}


@dataclass
class Detection:
    spans: list[Span]                     # 변환 대상 (S)
    blockers: list[Span]                  # 기밀 내용 (C)
    notes: list[str] = field(default_factory=list)


def _propagate(text: str, spans: list[Span]) -> list[Span]:
    """한 번 이름으로 확인된 값은 호칭 없이 다시 나와도 잡는다 ("홍길동 학생이 ... 홍길동이 ...")."""
    extra = []
    for s in spans:
        if s.type not in _PROPAGATE or s.confidence < scoring.AUTO_APPLY:
            continue
        for m in re.finditer(rf"(?<![가-힣A-Za-z]){re.escape(s.value)}", text):
            if m.start() == s.start:
                continue
            extra.append(Span(s.type, m.start(), m.end(), s.value, s.confidence, s.grade,
                              "같은 값이 다른 곳에서 탐지됨", s.source))
    return spans + extra


def _resolve(spans: list[Span]) -> list[Span]:
    """겹치면 사용자 지정 > 신뢰도 > 길이 순으로 하나만 남긴다."""
    ordered = sorted(spans, key=lambda s: (s.source != "manual", -s.confidence, -s.length))
    kept: list[Span] = []
    for s in ordered:
        if not any(s.overlaps(k) for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s.start)


def detect(text: str, base: dict, ignore=frozenset(), manual=()) -> Detection:
    cr = rules.code_ranges(text)
    raw = rules.detect_all(text, cr, base) + lexicon.detect_all(text, base)
    raw = _propagate(text, raw)
    raw = [s for s in raw if s.value not in ignore]
    for typ, value in manual:
        for m in re.finditer(re.escape(value), text):
            raw.append(Span(typ, m.start(), m.end(), value, 1.0, "S", "사용자가 직접 지정 (놓친 정보 신고)", "manual"))
    spans = _resolve(raw)
    notes = scoring.reident_boost(spans)
    blockers = [b for b in lexicon.detect_blockers(text, base, cr) if b.value not in ignore]
    return Detection(spans, blockers, notes)
