"""탐지 파이프라인: 규칙 → 사전·베이스 → 소형 모델(설정한 경우) → 겹침 정리 → 조합 위험."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import lexicon, rules, scoring
from .model import ModelError
from .span import Span

_PROPAGATE = {"name", "client", "school"}

COVERAGE_LABELS = {
    "rules": "규칙·사전",
    "model": "규칙·사전·소형 모델",
    "model_failed": "규칙·사전 (소형 모델 실패)",
    "model_skipped": "규칙·사전 (글이 길어 소형 모델 생략)",
}
MODEL_FAILED_NOTE = "소형 모델 검사에 실패해 규칙·사전으로만 검사했어요. 이름·주소 등을 놓쳤을 수 있으니 한 번 더 확인하세요."
# CPU 추론은 1만 자에 수 초가 걸린다 (backend local_ai 측정). 점검 창이 멈추지 않도록 이보다 긴 글은 모델을 건너뛰고 알린다
MODEL_MAX_CHARS = 8000
MODEL_SKIPPED_NOTE = (f"글이 길어({MODEL_MAX_CHARS:,}자 초과) 소형 모델 검사를 건너뛰었어요. 규칙·사전으로만 검사했으니 "
                      "나눠서 점검하거나 한 번 더 확인하세요.")


@dataclass
class Detection:
    spans: list[Span]                     # 변환 대상 (S)
    blockers: list[Span]                  # 기밀 내용 (C)
    notes: list[str] = field(default_factory=list)
    coverage: str = "rules"               # rules / model / model_failed / model_skipped


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


class KnownValues:
    """다른 글(같은 파일의 다른 칸 등)에서 이미 확인된 값. 문맥이 없어도 같은 값이면 잡는다.

    값이 수천 개여도 칸마다 한 번에 찾도록 정규식 하나로 묶어 둔다.
    """

    def __init__(self, items=()):  # [(유형, 값, 신뢰도)]
        self.info: dict[str, tuple[str, float]] = {}
        for typ, value, conf in items:
            if len(value) >= 2 and value not in self.info:
                self.info[value] = (typ, conf)
        alts = sorted(self.info, key=len, reverse=True)
        self.rx = (re.compile(r"(?<![가-힣A-Za-z0-9])(?:" + "|".join(map(re.escape, alts)) + ")")
                   if alts else None)

    def spans(self, text: str) -> list[Span]:
        if self.rx is None:
            return []
        out = []
        for m in self.rx.finditer(text):
            typ, conf = self.info[m.group()]
            out.append(Span(typ, m.start(), m.end(), m.group(), conf, "S", "같은 값이 다른 곳에서 탐지됨", "known"))
        return out


def _union_with_model(text: str, spans: list[Span]) -> list[Span]:
    """모델과 규칙이 같은 유형을 조금 다른 경계로 잡으면 합집합으로 가린다 (일부만 가려지는 일 방지)."""
    if not any(s.source == "model" for s in spans):
        return spans
    out: list[Span] = []
    for s in sorted(spans, key=lambda s: (s.type, s.start, s.end)):
        prev = out[-1] if out else None
        if (prev and prev.type == s.type and s.start < prev.end
                and "model" in (prev.source, s.source) and "manual" not in (prev.source, s.source)):
            start, end = prev.start, max(prev.end, s.end)
            best = max(prev, s, key=lambda x: x.confidence)
            reasons = [r for r in (prev.reason, s.reason) if r]
            out[-1] = Span(s.type, start, end, text[start:end], best.confidence, best.grade,
                           " + ".join(dict.fromkeys(reasons)), best.source)
        else:
            out.append(s)
    return out


def _resolve(spans: list[Span]) -> list[Span]:
    """겹치면 사용자 지정 > 신뢰도 > 길이 순으로 하나만 남긴다."""
    ordered = sorted(spans, key=lambda s: (s.source != "manual", -s.confidence, -s.length))
    kept: list[Span] = []
    for s in ordered:
        if not any(s.overlaps(k) for k in kept):
            kept.append(s)
    return sorted(kept, key=lambda s: s.start)


def detect(text: str, base: dict, ignore=frozenset(), manual=(), model=None, known=(),
           model_max_chars: int | None = MODEL_MAX_CHARS) -> Detection:
    """model: AegisModel 같은 2단계 탐지기(없으면 규칙·사전만). known: KnownValues 또는 [(유형, 값, 신뢰도)].
    model_max_chars: 화면을 멈추지 않으려고 두는 길이 제한. 작업 스레드(파일 검사)는 None으로 끝까지 검사한다."""
    cr = rules.code_ranges(text)
    raw = rules.detect_all(text, cr, base) + lexicon.detect_all(text, base)
    notes: list[str] = []
    coverage = "rules"
    if model is not None and model_max_chars is not None and len(text) > model_max_chars:
        coverage = "model_skipped"
        notes.append(MODEL_SKIPPED_NOTE)
    elif model is not None:
        try:
            raw += model.detect(text)
            coverage = "model"
        except ModelError:
            coverage = "model_failed"
            notes.append(MODEL_FAILED_NOTE)
    raw = _union_with_model(text, raw)
    raw = _propagate(text, raw)
    if known:
        raw += (known if isinstance(known, KnownValues) else KnownValues(known)).spans(text)
    raw = [s for s in raw if s.value not in ignore]
    for typ, value in manual:
        for m in re.finditer(re.escape(value), text):
            raw.append(Span(typ, m.start(), m.end(), value, 1.0, "S", "사용자가 직접 지정 (놓친 정보 신고)", "manual"))
    spans = _resolve(raw)
    notes += scoring.reident_boost(spans)
    blockers = [b for b in lexicon.detect_blockers(text, base, cr) if b.value not in ignore]
    return Detection(spans, blockers, notes, coverage)
