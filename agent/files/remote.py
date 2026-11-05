"""서버 처리 모드의 파일 검사: 업로드 → 결과 → 보호본 받기 (backend /files/sanitize, /files/{work_id}/download).

1. 이 PC: 조직 베이스(코드네임·고객사·사내 도메인)를 태그로 바꾸고, 기밀 표기·사내 소스코드가 있으면 막는다
   (OrgProcessor). 이 단계에서 숨긴 시트·행·열, 메모, 문서 속성이 빠지므로 서버에는 보이는 값만 간다.
   원래 파일 이름도 보내지 않는다 ("upload.xlsx").
2. 서버: 그 사본에서 개인정보를 찾아 [이름_1] 같은 자리표시로 바꾼 보호본을 만든다.
3. 이 PC: 보호본을 바로 받는다(서버는 한 번만 내준다). 바이트는 메모리에만 두고 사용자가 저장할 때 쓴다.
   서버 응답에는 무엇을 가렸는지가 없어서, 올린 사본과 받은 보호본을 칸끼리 맞춰 유형·개수를 센다.
   받은 보호본에 이 PC에서 가린 값이나 기밀 표기가 다시 나타나면 쓰지 않는다.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..server.align import LABEL_TYPE, PLACEHOLDER, align
from ..server.client import ServerError
from .processor import (FALLBACK_REASONS, MAX_FILE_BYTES, REASON_MESSAGES, FileReport, OrgProcessor, available,
                        suggested_name)

STAGES = {
    "check": "팀 서버 연결과 탐지 범위를 확인하는 중…",
    "local": "이 PC에서 조직 베이스·기밀 표기를 확인하는 중…",
    "upload": "팀 서버에서 검사하는 중… 칸이 많으면 시간이 걸릴 수 있어요",
    "download": "보호본을 받는 중…",
}
_ERROR_REASONS = {"unreachable": "SERVER_UNREACHABLE", "expired": "FILE_NOT_AVAILABLE",
                  "rejected": "SERVER_REJECTED", "bad_response": "SERVER_ERROR", "failed": "SERVER_ERROR"}
# 요청 형식 문제 (앱과 서버 버전이 맞지 않을 때)
_REQUEST_REASONS = frozenset({"EXPECTED_MULTIPART", "INVALID_MULTIPART", "EXPECTED_ONE_FILE"})


@dataclass
class Comparison:
    """올린 사본과 서버 보호본의 차이."""
    found: Counter = field(default_factory=Counter)    # 서버가 가린 값 (앱 유형별, 나온 횟수)
    tokens: set = field(default_factory=set)           # 서버가 넣은 자리표시
    values: dict = field(default_factory=dict)         # 원래 값 → 자리표시 (위치가 확정된 것만, 파일 이름용)
    segments: tuple = ()                               # 서버 보호본을 다시 읽은 조각 (재검사용)


def compare_copies(sent: bytes, received: bytes) -> Comparison:
    """칸 위치(시트 순서·셀 주소)는 보호본에서도 그대로다. 다르면 ValueError."""
    from .xlsx.sanitizer import read_xlsx

    before, after = read_xlsx(sent), read_xlsx(received)
    if after.comments or after.hidden_sheets or after.hidden_rows or after.hidden_columns:
        raise ValueError("protected copy has hidden content")
    old = {s.id: s.text for s in before.segments if s.exportable}
    new = {s.id: s.text for s in after.segments if s.exportable}
    if len(after.visible_sheets) != len(before.visible_sheets) or not set(new) <= set(old):
        raise ValueError("protected copy does not match the uploaded copy")
    out = Comparison(segments=tuple(after.segments))
    for key, text in old.items():
        masked = new.get(key, "")
        if masked == text:
            continue
        mine = Counter(m.group() for m in PLACEHOLDER.finditer(text))  # 원래 칸에 있던 [..._n] 글자는 빼고 센다
        for m in PLACEHOLDER.finditer(masked):
            if mine[m.group()]:
                mine[m.group()] -= 1
                continue
            out.found[LABEL_TYPE.get(m.group(1), "other")] += 1
            out.tokens.add(m.group())
        for placed in align(text, masked):
            if placed.exact and placed.value:
                out.values.setdefault(placed.value, placed.token)
    return out


def server_file_name(filename: str, base: dict, session, ignore, values: dict) -> str:
    """보호본 이름: 서버가 칸에서 가린 값은 같은 자리표시로 바꾸고, 나머지는 이 PC 엔진으로 가린다.

    "홍길동_상담.xlsx" → "[이름_1]_상담_보호본.xlsx" (이름 자체는 서버로 보내지 않는다)
    """
    stem = Path(filename).stem
    for value in sorted(values, key=len, reverse=True):
        if len(value) >= 2:
            stem = stem.replace(value, values[value])
    return suggested_name(stem + ".xlsx", base, session, ignore)


def _failed(reason: str, **kw) -> FileReport:
    if reason in _REQUEST_REASONS:
        reason = "SERVER_REJECTED"
    elif reason not in REASON_MESSAGES:
        reason = "SERVER_ERROR"
    return FileReport("FAILED", reason, where="server", coverage="server", fallback=reason in FALLBACK_REASONS, **kw)


def inspect_xlsx_server(path, base: dict, session, server, *, ignore=frozenset(), progress=None) -> FileReport:
    """서버로 xlsx 하나를 검사해 보호본을 받는다. 원본 파일은 바꾸지 않고, 서버로 보내지도 않는다."""
    say = progress or (lambda stage: None)
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        return _failed("UNSUPPORTED_FILE_TYPE")
    if server is None:
        return _failed("SERVER_UNSET")
    if not available():
        return _failed("DEPENDENCY_MISSING")
    from .xlsx.sanitizer import Summary, sanitize_xlsx

    # 서버가 처리하지 못할 파일은 보내지 않는다: 서버 파일 검사는 모델이 있어야 하고(없으면 503),
    # 서버는 그 답을 하기 전에 파일을 끝까지 받는다. /health가 탐지 범위를 알려 준다 (backend#11).
    say("check")
    try:
        health = server.health()
    except ServerError as exc:
        return _failed(_ERROR_REASONS.get(exc.kind, "SERVER_ERROR"))
    if not health.ok:
        return _failed("SERVER_ERROR")
    if health.model is False:
        return _failed("SERVER_RULES_ONLY")

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return _failed("FILE_TOO_LARGE")
        data = path.read_bytes()
    except OSError:
        return _failed("READ_ERROR")

    say("local")
    org = OrgProcessor(base, session, ignore=ignore)
    local = sanitize_xlsx(data, f"kpguard-{session.created:.0f}", org, filename=path.name)
    del data
    mine = dict(org_found=org.found, hidden_found=org.hidden_found, blocker_types=org.blocker_types,
                blocker_reasons=list(dict.fromkeys(org.blocker_reasons)))
    if local.status == "BLOCKED":
        return FileReport("BLOCKED", local.reason, "C", where="server", coverage="server", **mine)
    if local.status != "READY":
        return _failed(local.reason or "PROCESSOR_ERROR", **mine)

    say("upload")
    try:
        res = server.sanitize_file(local.file_bytes)
    except ServerError as exc:
        return _failed(_ERROR_REASONS.get(exc.kind, "SERVER_ERROR"), **mine)
    if res.status == "BLOCKED":
        return FileReport("BLOCKED", "SERVER_CONFIDENTIAL", "C", where="server", coverage="server", **mine)
    if res.status != "READY" or not res.work_id:
        return _failed(res.reason or "SERVER_ERROR", **mine)

    say("download")
    try:
        copy = server.download_file(res.work_id)
    except ServerError as exc:
        return _failed(_ERROR_REASONS.get(exc.kind, "SERVER_ERROR"), **mine)
    try:
        diff = compare_copies(local.file_bytes, copy)
        clean = org.verify(None, diff.segments)  # 이 PC에서 가린 값·기밀 표기가 되살아나지 않았는지
    except Exception:  # 예외 메시지에 칸 내용이 섞일 수 있어 보여 주지 않는다
        return _failed("SERVER_COPY_INVALID", **mine)
    if not clean:
        return _failed("SERVER_COPY_INVALID", **mine)

    s, srv = local.summary, res.summary
    summary = Summary(s.changed_cells + srv.get("changed_cells", 0),
                      s.removed_comments + srv.get("removed_comments", 0),
                      s.removed_sheets + srv.get("removed_sheets", 0),
                      s.removed_rows + srv.get("removed_rows", 0),
                      s.removed_columns + srv.get("removed_columns", 0))
    found = diff.found + org.found
    grade = "S" if found or res.grade == "S" or local.grade == "S" else "O"
    report = FileReport("READY", None, grade, copy, summary, found, where="server", coverage="server",
                        server_tokens=frozenset(diff.tokens), **mine)
    report.suggested_name = server_file_name(path.name, base, session, ignore, diff.values)
    return report
