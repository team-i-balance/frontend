"""가짜 값 풀 (§5 대체값 생성 규칙).

- 자연스럽지만 흔하지 않은 이름 → AI가 스스로 지어낸 흔한 이름과 섞이지 않게
- 받침 유무가 원래 이름과 같은 이름 → 조사가 어색해지지 않게
- 티 나는 가짜 범위: 전화 0000 국번, 이메일 example.com, IP 192.0.2.x (문서용 대역)
"""
from __future__ import annotations

import re

from .josa import has_batchim

FAKE_SURNAMES = ["탁", "편", "봉", "어", "변", "국", "옥", "육", "제", "빈", "좌", "예", "석", "추"]
GIVEN_BATCHIM = ["서윤", "지환", "하람", "승민", "태윤", "재민", "은솔", "시원", "도윤", "가람", "한결",
                 "이현", "주원", "새롬"]
GIVEN_OPEN = ["소희", "도아", "서아", "하나", "지우", "예나", "수아", "하루", "유나", "채이", "다예",
              "시우", "은채", "나래"]
GIVEN1_BATCHIM = ["윤", "현", "민", "준", "솔", "람"]
GIVEN1_OPEN = ["희", "아", "우", "나", "서", "하"]

SCHOOLS = ["한빛고", "새솔고", "누리고", "다온고", "가람고", "온새미고"]
CLIENTS = ["대한상사", "미래물산", "가온산업", "새길무역", "온누리상사", "푸른유통"]


def _probe(pool: list[str], seed: int, taken) -> str:
    for i in range(len(pool)):
        cand = pool[(seed + i) % len(pool)]
        if not taken(cand):
            return cand
    return pool[seed % len(pool)]


def fake_name(original: str, seed: int, taken) -> str:
    surname_len = 2 if len(original) == 4 else 1
    given_len = max(1, len(original) - surname_len)
    bat = has_batchim(original)
    if given_len == 1:
        pool = GIVEN1_BATCHIM if bat else GIVEN1_OPEN
    else:
        pool = GIVEN_BATCHIM if bat else GIVEN_OPEN
    candidates = [s + g for s in FAKE_SURNAMES for g in pool]
    return _probe(candidates, seed, taken)


def fake_phone(original: str, n: int) -> str:
    """국번(가운데)을 0으로, 끝 4자리를 일련번호로. 원래 구분자 위치는 유지한다."""
    digits = re.sub(r"\D", "", original)
    prefix = 2 if digits.startswith("02") else 3
    new = digits[:prefix] + "0" * (len(digits) - prefix - 4) + f"{n % 10000:04d}"
    it = iter(new)
    return "".join(next(it) if ch.isdigit() else ch for ch in original)


def fake_email(n: int) -> str:
    return f"user{n}@example.com"


def fake_ip(n: int) -> str:
    return f"192.0.2.{n % 254 + 1}"


def pick(pool: list[str], seed: int, taken) -> str:
    return _probe(pool, seed, taken)
