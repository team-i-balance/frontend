"""0단계: 규칙 기반 탐지 (모델 없음). §3

주민번호는 2020년 10월 이후 발급분의 뒷자리가 임의번호라 체크섬이 맞지 않을 수 있다.
그래서 체크섬은 가산점으로만 쓰고, 불일치를 이유로 제외하지 않는다.
"""
from __future__ import annotations

import math
import re
from datetime import date

from .span import Span

# ---------------------------------------------------------------- 문맥 점수
_PLUS = ("주민", "연락처", "전화", "휴대폰", "핸드폰", "계좌", "고객", "환자", "학생",
         "카드", "생년월일", "주소", "입금", "송금")
_MINUS_KO = ("주문번호", "주문", "버전", "해시", "커밋", "빌드", "송장", "티켓", "일련번호", "시리얼")
_MINUS_EN = re.compile(r"\b(id|version|ver|hash|sha\d*|md5|uuid|commit|build|ticket|order|serial)\b", re.I)


def context_score(text: str, start: int, end: int, width: int = 15) -> float:
    window = text[max(0, start - width):start] + " " + text[end:end + width]
    score = 0.0
    if any(w in window for w in _PLUS):
        score += 0.1
    if any(w in window for w in _MINUS_KO) or _MINUS_EN.search(window):
        score -= 0.3
    return score


# ---------------------------------------------------------------- 코드 문맥
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_CODE_LINE = re.compile(
    r"^\s*(def |class |import |from \S+ import |package |public |private |protected |function |"
    r"const |let |var |#include|return\b|if\s*\(|for\s*\(|while\s*\(|\}|@\w+)|[;{]\s*$"
)


def code_ranges(text: str) -> list[tuple[int, int]]:
    """``` 펜스 블록 + 펜스 없이 붙여넣은 코드(코드 같은 줄 5줄 이상 연속)."""
    ranges = [(m.start(), m.end()) for m in _FENCE.finditer(text)]
    offset, run_start, run_end, run_count = 0, None, 0, 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and _CODE_LINE.search(line):
            if run_start is None:
                run_start = offset
            run_count += 1
            run_end = offset + len(line.rstrip("\r\n"))
        elif stripped:
            if run_start is not None and run_count >= 5:
                ranges.append((run_start, run_end))
            run_start, run_count = None, 0
        offset += len(line)
    if run_start is not None and run_count >= 5:
        ranges.append((run_start, run_end))
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return merged


def _in_code(pos: int, ranges) -> bool:
    return any(s <= pos < e for s, e in ranges)


# ---------------------------------------------------------------- 주민/외국인등록번호
_RRN = re.compile(r"(?<![\d-])(\d{6})\s?(-?)\s?([1-8]\d{6})(?![\d-])")
_RRN_W = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)


def _rrn_checksum(d: str) -> bool:
    total = sum(int(d[i]) * _RRN_W[i] for i in range(12))
    return (11 - total % 11) % 10 == int(d[12])


def _rrn(text: str) -> list[Span]:
    out = []
    for m in _RRN.finditer(text):
        front, hyphen, back = m.groups()
        yy, mm, dd, g = int(front[:2]), int(front[2:4]), int(front[4:]), int(back[0])
        century = 1900 if g in (1, 2, 5, 6) else 2000
        try:
            date(century + yy, mm, dd)
        except ValueError:
            continue
        if century + yy > date.today().year:
            continue
        conf = 0.8 if hyphen else 0.65
        reasons = ["주민번호 형식", "생년월일·성별 자리 유효"]
        if _rrn_checksum(front + back):
            conf += 0.15
            reasons.append("검증번호 일치")
        else:
            reasons.append("검증번호 불일치(2020.10 이후 발급분 가능)")
        conf += context_score(text, m.start(), m.end())
        typ = "rrn" if g <= 4 else "frn"
        out.append(Span(typ, m.start(), m.end(), m.group(), min(conf, 0.99), "S", " + ".join(reasons)))
    return out


# ---------------------------------------------------------------- 카드 (Luhn)
_CARD = re.compile(r"(?<![\d-])\d{4}([- ]?)\d{4}\1\d{4}\1\d{4}(?![\d-])")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _card(text: str) -> list[Span]:
    out = []
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        window = text[max(0, m.start() - 15):m.start()]
        if _luhn(digits):
            out.append(Span("card", m.start(), m.end(), m.group(), 0.9, "S", "카드번호 형식 + Luhn 검증 통과"))
        elif "카드" in window:
            out.append(Span("card", m.start(), m.end(), m.group(), 0.65, "S", "카드번호 형식 + 문맥 '카드'"))
    return out


# ---------------------------------------------------------------- 사업자등록번호
_BIZ = re.compile(r"(?<![\d-])(\d{3})-(\d{2})-(\d{5})(?![\d-])")


def _biz_ok(d: str) -> bool:
    w = (1, 3, 7, 1, 3, 7, 1, 3, 5)
    total = sum(int(d[i]) * w[i] for i in range(9)) + int(d[8]) * 5 // 10
    return (10 - total % 10) % 10 == int(d[9])


def _bizno(text: str) -> list[Span]:
    out = []
    for m in _BIZ.finditer(text):
        digits = "".join(m.groups())
        window = text[max(0, m.start() - 15):m.start()]
        if _biz_ok(digits):
            out.append(Span("bizno", m.start(), m.end(), m.group(), 0.85, "S", "사업자번호 형식 + 검증식 통과"))
        elif "사업자" in window:
            out.append(Span("bizno", m.start(), m.end(), m.group(), 0.65, "S", "사업자번호 형식 + 문맥 '사업자'"))
    return out


# ---------------------------------------------------------------- 계좌번호 (문맥 필요)
_ACCOUNT = re.compile(r"(?<![\d-])\d{2,6}(?:-\d{2,7}){1,3}(?![\d-])")
_BANK = ("계좌", "은행", "입금", "송금", "이체", "국민", "신한", "우리", "하나", "농협", "기업",
         "카카오뱅크", "토스", "새마을", "우체국", "SC제일", "씨티")


def _account(text: str) -> list[Span]:
    out = []
    for m in _ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if not 10 <= len(digits) <= 14:
            continue
        window = text[max(0, m.start() - 20):m.start()] + text[m.end():m.end() + 10]
        hit = next((w for w in _BANK if w in window), None)
        if hit:
            out.append(Span("account", m.start(), m.end(), m.group(), 0.8, "S", f"계좌번호 형식 + 문맥 '{hit}'"))
    return out


# ---------------------------------------------------------------- 전화번호
_MOBILE = re.compile(r"(?<![\d-])(01[016789])([-. ]?)(\d{3,4})\2(\d{4})(?![\d-])")
_LANDLINE = re.compile(r"(?<![\d-])(02|0[3-6][1-5]|070)([-. ]?)(\d{3,4})\2(\d{4})(?![\d-])")


def _phone(text: str, base: dict) -> list[Span]:
    white = set(base.get("whitelist", {}).get("phones", []))
    out = []
    for rx, conf, why in ((_MOBILE, 0.9, "휴대전화 형식(01X)"), (_LANDLINE, 0.75, "유선전화 형식 + 실재 국번")):
        for m in rx.finditer(text):
            if m.group() in white or set(m.group(3)) == {"0"}:  # 0000 국번 = 우리가 만든 가짜 값
                continue
            c = conf + context_score(text, m.start(), m.end())
            out.append(Span("phone", m.start(), m.end(), m.group(), min(c, 0.99), "S", why))
    return out


# ---------------------------------------------------------------- 이메일, IP
_EMAIL = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IP = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")
_IP_SKIP = {"127.0.0.1", "0.0.0.0", "255.255.255.255", "255.255.255.0"}


def _email(text: str, base: dict) -> list[Span]:
    white = set(base.get("whitelist", {}).get("emails", []))
    out = []
    for m in _EMAIL.finditer(text):
        domain = m.group().rsplit("@", 1)[1].lower()
        if m.group() in white or domain.startswith("example."):
            continue
        out.append(Span("email", m.start(), m.end(), m.group(), 0.9, "S", "이메일 형식"))
    return out


def _ip(text: str, cr) -> list[Span]:
    out = []
    for m in _IP.finditer(text):
        ip = m.group()
        if ip in _IP_SKIP or ip.startswith("192.0.2."):  # 192.0.2.x = 문서용 대역(가짜 값)
            continue
        before = text[max(0, m.start() - 10):m.start()].lower()
        if "v" in before[-2:] or "버전" in before or "version" in before:
            continue
        conf = 0.75 if ip.startswith(("10.", "192.168.", "172.")) else 0.7
        why = "IPv4 형식" + (" (사설 대역 → 내부망 정보)" if conf > 0.7 else "")
        if _in_code(m.start(), cr):
            conf -= 0.1
        out.append(Span("ip", m.start(), m.end(), ip, conf, "S", why))
    return out


# ---------------------------------------------------------------- API 키·토큰·비밀번호
_SECRET_PREFIX = re.compile(
    r"(?<![A-Za-z0-9])(sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
    r"AIza[0-9A-Za-z_\-]{35}|glpat-[A-Za-z0-9_\-]{20,})"
)
_SECRET_KV = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|비밀번호|비번|패스워드|암호)"
    r"(?:는|은|가)?\s*[:=]?\s*[\"']?([^\s\"',;]{4,})"
)
_TOKEN = re.compile(r"(?<![A-Za-z0-9_\-+/=])[A-Za-z0-9_\-+/=]{24,}(?![A-Za-z0-9_\-+/=])")


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def _secret(text: str, cr) -> list[Span]:
    out = []
    for m in _SECRET_PREFIX.finditer(text):
        out.append(Span("secret", m.start(), m.end(), m.group(), 0.95, "S", "알려진 키 접두어 패턴"))
    for m in _SECRET_KV.finditer(text):
        value = m.group(2)
        # "비밀번호 변경하는 법" 같은 일반 문장을 피하려고 숫자·기호가 섞인 값만
        if not re.search(r"[0-9!@#$%^&*_\-]", value) or re.fullmatch(r"[가-힣]+", value):
            continue
        out.append(Span("secret", m.start(2), m.end(2), value, 0.85, "S", f"'{m.group(1)}' 뒤의 값"))
    for m in _TOKEN.finditer(text):
        tok = m.group()
        if re.fullmatch(r"[0-9a-fA-F\-]+", tok):  # 순수 16진수/UUID → 해시일 가능성
            continue
        if not (re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok) and re.search(r"\d", tok)):
            continue
        ent = shannon_entropy(tok)
        if ent < 4.0:
            continue
        conf = 0.55
        window = text[max(0, m.start() - 20):m.start()].lower()
        if any(w in window for w in ("key", "token", "secret", "키", "토큰")):
            conf += 0.2
        if _in_code(m.start(), cr):
            conf -= 0.15
        out.append(Span("secret", m.start(), m.end(), tok, conf, "S", f"고엔트로피 문자열(엔트로피 {ent:.1f})"))
    return out


def detect_all(text: str, cr, base: dict) -> list[Span]:
    spans: list[Span] = []
    spans += _rrn(text)
    spans += _card(text)
    spans += _bizno(text)
    spans += _account(text)
    spans += _phone(text, base)
    spans += _email(text, base)
    spans += _ip(text, cr)
    spans += _secret(text, cr)
    return spans
