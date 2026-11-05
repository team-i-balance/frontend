"""위험도·등급 판정 (§3 위험도, §4 등급, §12 조합 재식별의 최소판)."""
from __future__ import annotations

from .span import Span

AUTO_APPLY = 0.6       # 이 이상이면 자동 변환, 미만은 하이라이트만 ("확인 필요")
QUASI = {"age", "address", "school", "grade_class", "dob"}
QUASI_LABEL = {"age": "나이", "address": "지역", "school": "학교", "grade_class": "학년·반", "dob": "생년월일"}


def reident_boost(spans: list[Span]) -> list[str]:
    """준식별자가 3종 이상 모이면 하나씩은 안전해도 합치면 특정인이 될 수 있다 → 자동 변환으로 올린다."""
    types = sorted({s.type for s in spans if s.type in QUASI})
    if len(types) < 3:
        return []
    for s in spans:
        if s.type in QUASI:
            s.confidence = max(s.confidence, 0.7)
            if "조합 재식별" not in s.reason:
                s.reason += " · 조합 재식별 위험"
    combo = "+".join(QUASI_LABEL[t] for t in types)
    return [f"조합 재식별 위험: {combo}이(가) 함께 있어 특정인을 알아볼 수 있어요. 함께 변환했어요."]


def overall_grade(items) -> str:
    """가장 높은 등급 기준. 차단(C)은 사용자가 '빼고 보내기'나 '차단 해제'를 하지 않은 기밀 항목이 있을 때."""
    if any(it.is_blocker and it.applied and it.strategy == "block" for it in items):
        return "C"
    if any(not it.is_blocker for it in items):
        return "S"
    return "O"
