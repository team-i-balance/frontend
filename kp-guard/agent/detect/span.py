"""탐지 결과 데이터 구조 (§5: Span = {type, start, end, value, confidence, grade})."""
from __future__ import annotations

from dataclasses import dataclass

GRADE_ORDER = {"O": 0, "S": 1, "C": 2}

TYPE_LABELS = {
    "rrn": "주민등록번호",
    "frn": "외국인등록번호",
    "card": "카드번호",
    "bizno": "사업자등록번호",
    "account": "계좌번호",
    "phone": "전화번호",
    "email": "이메일",
    "ip": "IP 주소",
    "secret": "API 키·비밀번호",
    "name": "이름",
    "age": "나이",
    "address": "주소",
    "school": "학교",
    "grade_class": "학년·반",
    "codename": "코드네임",
    "client": "고객사",
    "internal_host": "사내 도메인",
    "classified": "기밀 표기",
    "source_code": "사내 소스코드",
    "other": "기타(직접 신고)",
}

# 하이라이트 색 묶음
CATEGORY = {
    "rrn": "identifier", "frn": "identifier",
    "card": "finance", "bizno": "finance", "account": "finance",
    "phone": "contact", "email": "contact", "ip": "contact",
    "secret": "secret",
    "name": "person",
    "age": "quasi", "address": "quasi", "school": "quasi", "grade_class": "quasi",
    "codename": "org", "client": "org", "internal_host": "org",
    "classified": "blocker", "source_code": "blocker",
    "other": "manual",
}


@dataclass
class Span:
    type: str
    start: int
    end: int
    value: str
    confidence: float
    grade: str = "S"
    reason: str = ""
    source: str = "rule"  # rule / lexicon / base / manual

    @property
    def key(self) -> tuple[str, str]:
        """같은 값은 같은 결정을 공유한다 (한 번 되돌리면 모든 등장 위치가 함께 되돌려짐)."""
        return (self.type, self.value)

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end
