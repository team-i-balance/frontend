"""1단계(경량판): 사전 + 문맥 기반 이름·주소·준식별자 + 조직 베이스 대조. §3

프로토타입은 정규식과 작은 사전만 쓴다.
Kiwi 형태소 분석(NNP 후보), 행정구역 전체 트리, Aho-Corasick, 해시 명단 대조는 Phase 3에서 교체한다.
"""
from __future__ import annotations

import re

from .span import Span

# ---------------------------------------------------------------- 이름
SURNAMES_1 = set(
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진지엄채원천"
    "방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가복태목형피두감음빈동온호좌"
)
SURNAMES_2 = {"남궁", "황보", "제갈", "선우", "독고", "사공", "서문", "동방"}

_TITLES = ("님", "씨", "고객", "학생", "선생님", "선생", "과장", "대리", "부장", "차장", "팀장", "사원",
           "주임", "실장", "이사", "대표", "교수", "환자", "학부모", "어머님", "아버님")
_TITLE_WORDS = {"과장", "부장", "차장", "대리", "사원", "팀장", "이사", "대표", "사장", "주임", "실장",
                "원장", "교수", "선생", "기사", "반장", "회장", "고객", "학생", "님", "씨"}
_NAME_STOP = {"이번", "이제", "이미", "정말", "정도", "고객", "선생", "오늘", "모두", "한번", "신규", "전체",
              "조금", "하나", "우리", "안내", "공지", "문의", "최근", "최종", "방금", "주로", "지난", "강사",
              "조사", "이분", "그분", "저분", "여러분", "담당", "담임", "부모", "어머", "아버", "기사", "원장",
              "사장", "차장", "이사", "주임", "반장", "회장", "전문", "신입", "위원", "구독", "한분", "학부"}
_PARTICLES = set("는은가이을를도와과의랑")
_NAME = re.compile(
    r"(?<![가-힣])([가-힣]{2,4})(?=\s?(?:" + "|".join(_TITLES) + r"|군(?![가-힣])|양(?![가-힣])|에게|한테|\())"
)
_PHONE_NEAR = re.compile(r"01[016789][-. ]?\d{3,4}[-. ]?\d{4}")


def _names(text: str) -> list[Span]:
    out = []
    for m in _NAME.finditer(text):
        cand = m.group(1)
        if len(cand) == 4 and cand[-1] in _PARTICLES:
            cand = cand[:-1]
        if cand[:2] in SURNAMES_2:
            given = cand[2:]
        elif cand[0] in SURNAMES_1:
            given = cand[1:]
        else:
            continue
        if not 1 <= len(given) <= 2 or cand in _NAME_STOP or given in _TITLE_WORDS:
            continue
        start, end = m.start(1), m.start(1) + len(cand)
        follow = text[end:end + 4].lstrip()
        if follow.startswith(("에게", "한테")):
            conf, why = 0.6, "성씨 + '에게/한테'"
        elif follow.startswith("("):
            conf, why = 0.7, "성씨 + 괄호 부가정보"
        else:
            conf, why = 0.8, f"성씨 사전 + 호칭 '{follow[:2].strip()}'"
        if _PHONE_NEAR.search(text[end:end + 25]):
            conf += 0.1
            why += " + 근처 전화번호"
        out.append(Span("name", start, end, cand, min(conf, 0.95), "S", why, "lexicon"))
    return out


# ---------------------------------------------------------------- 나이
_AGE = re.compile(r"(?<!\d)(\d{1,3})\s?(세|살)(?!대|기|트|금|무|율|입|수|계|상)")


def _ages(text: str) -> list[Span]:
    out = []
    for m in _AGE.finditer(text):
        if 1 <= int(m.group(1)) <= 120:
            out.append(Span("age", m.start(), m.end(), m.group(), 0.6, "S", "나이 표현(준식별자)", "lexicon"))
    return out


# ---------------------------------------------------------------- 주소
_SIDO = (r"(?:서울(?:특별시|시)?|부산(?:광역시|시)?|대구(?:광역시|시)?|인천(?:광역시|시)?|광주(?:광역시|시)?|"
         r"대전(?:광역시|시)?|울산(?:광역시|시)?|세종(?:특별자치시|시)?|경기도?|강원(?:특별자치도|도)?|"
         r"충청북도|충북|충청남도|충남|전북(?:특별자치도)?|전라북도|전라남도|전남|경상북도|경북|경상남도|경남|"
         r"제주(?:특별자치도|도)?)")
_DETAIL = r"(?:\s+[가-힣0-9]{1,10}(?:동|읍|면|리|로|길)(?:\s*\d{1,5}(?:-\d{1,5})?(?:번지|번길)?)?)"
_ADDR_FULL = re.compile(
    rf"(?<![가-힣])(?P<region>{_SIDO}\s+[가-힣]{{1,6}}(?:시|군|구)(?:\s+[가-힣]{{1,6}}구)?)(?P<detail>{_DETAIL}?)"
)
_ADDR_PART = re.compile(
    r"(?<![가-힣])(?P<region>[가-힣]{1,6}(?:시|군|구))"
    r"(?P<detail>\s+[가-힣0-9]{1,10}(?:동|읍|면|로|길)\s*\d{1,5}(?:-\d{1,5})?(?:번지)?)"
)


def _addresses(text: str) -> list[Span]:
    out = []
    for rx in (_ADDR_FULL, _ADDR_PART):
        for m in rx.finditer(text):
            detail = m.group("detail") or ""
            if re.search(r"\d", detail):
                conf, why = 0.85, "행정구역 + 동/도로명 + 번지"
            elif detail:
                conf, why = 0.75, "행정구역 + 동/도로명"
            else:
                conf, why = 0.5, "시/도 + 시/군/구 (지역만)"
            out.append(Span("address", m.start(), m.end(), m.group(), conf, "S", why, "lexicon"))
    return out


def address_region(value: str) -> str:
    """일반화용: 동·번지를 떼고 시/군/구까지만 남긴다."""
    m = _ADDR_FULL.match(value) or _ADDR_PART.match(value)
    return m.group("region") if m else ""


# ---------------------------------------------------------------- 학교, 학년·반
_SCHOOL = re.compile(
    r"(?<![가-힣A-Za-z])[가-힣A-Za-z]{1,12}?(?:여자고등학교|여자중학교|초등학교|중학교|고등학교|대학교|"
    r"마이스터고|과학고|외고|여고|여중|공고|상고)"
)
_GRADE_CLASS = re.compile(r"(?<!\d)(\d{1,2})\s?학년\s?(\d{1,2})\s?반")


def _schools(text: str, base: dict) -> list[Span]:
    out = []
    for m in _SCHOOL.finditer(text):
        out.append(Span("school", m.start(), m.end(), m.group(), 0.65, "S", "학교명 패턴(준식별자)", "lexicon"))
    for name in base.get("schools", {}):
        for m in re.finditer(rf"(?<![A-Za-z가-힣]){re.escape(name)}(?![A-Za-z])", text):
            out.append(Span("school", m.start(), m.end(), name, 0.8, "S", "조직 베이스: 학교 목록", "base"))
    for m in _GRADE_CLASS.finditer(text):
        out.append(Span("grade_class", m.start(), m.end(), m.group(), 0.6, "S", "학년+반(준식별자)", "lexicon"))
    return out


# ---------------------------------------------------------------- 조직 베이스
def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _org(text: str, base: dict) -> list[Span]:
    out = []
    if base.get("codenames"):
        rx = re.compile(rf"(?:프로젝트\s?)?(?:{_alt(base['codenames'])})(?:\s?프로젝트)?")
        for m in rx.finditer(text):
            out.append(Span("codename", m.start(), m.end(), m.group(), 0.85, "S",
                            "조직 베이스: 미공개 프로젝트 코드네임", "base"))
    if base.get("clients"):
        for m in re.finditer(_alt(base["clients"]), text):
            out.append(Span("client", m.start(), m.end(), m.group(), 0.85, "S", "조직 베이스: 고객사 목록", "base"))
    if base.get("internal_domains"):
        rx = re.compile(rf"[A-Za-z0-9.-]*(?:{_alt(base['internal_domains'])})", re.I)
        for m in rx.finditer(text):
            # 이메일 주소의 도메인 부분이면 이메일 탐지에 맡긴다
            if m.start() > 0 and text[m.start() - 1] == "@":
                continue
            out.append(Span("internal_host", m.start(), m.end(), m.group(), 0.85, "S", "조직 베이스: 내부 도메인", "base"))
    return out


ORG_TYPES = frozenset({"codename", "client", "internal_host"})


def org_spans(text: str, base: dict, ignore=frozenset()) -> list[Span]:
    """조직 베이스(코드네임·고객사·사내 도메인)가 나온 모든 자리.

    서버 처리 모드에서는 서버에 조직 베이스가 없어 이 PC에서 먼저 가린다. 같은 값이 여러 번 나오면 모두 가려야 한다.
    """
    return [s for s in _org(text, base) if s.value not in ignore]


def detect_all(text: str, base: dict) -> list[Span]:
    return _names(text) + _ages(text) + _addresses(text) + _schools(text, base) + _org(text, base)


def detect_blockers(text: str, base: dict, cr) -> list[Span]:
    """기밀(C): '가려도 보내면 안 되는' 내용. 차단 후 사내 AI로 안내 (§4)."""
    out = []
    for marker in base.get("classified_markers", []):
        for m in re.finditer(re.escape(marker), text):
            out.append(Span("classified", m.start(), m.end(), m.group(), 0.9, "C",
                            f"'{marker}' 등급 표기 → 문서 내용 자체가 기밀일 수 있음", "base"))
    markers = (base.get("internal_code_markers", []) + base.get("internal_domains", [])
               + base.get("codenames", []))
    for s, e in cr:
        block = text[s:e]
        hit = next((mk for mk in markers if mk in block), None)
        if hit:
            lines = block.count("\n") + 1
            out.append(Span("source_code", s, e, block, 0.85, "C",
                            f"코드 블록({lines}줄)에 사내 식별자 '{hit}' 포함 → 사내 소스코드로 판단", "base"))
    return out
