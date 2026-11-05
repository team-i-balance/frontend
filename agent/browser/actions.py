"""입력창 칩 동작 (브라우저 확장·보호된 CLI 입력창·원래 입력창 오버레이가 함께 쓴다).

칩 흐름 — 별도 창 없이 칩을 펼쳐 마무리한다:
  1. 하이라이트를 누르면 칩 두 개: 마스킹 / 원문유지
  2-a. 마스킹 → 바뀔 값만 위에 보여 주고 아래 칩으로 고른다: 적용(같은 값 모두) / 모두 적용
       적용하면 입력창의 그 자리만 바꾼다 (mask가 고칠 자리를 돌려주고, 입력창이 바꾼 뒤 commit)
  2-b. 원문유지 → 사유 칩 셋: 업무상 필요 / 오탐 / 승인된 개인정보
  3. 남은 하이라이트가 없어지면 이 입력의 결정을 한 번 기록한다 (서버 /process/decide, 이 PC 로그)
하이라이트가 남아 있는 동안 입력창은 Enter 전송을 막는다 (각 입력창 쪽에서).
원문·매핑은 이 PC 메모리에만 두고 로그에는 유형·건수·사유만 남긴다.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from ..detect import lexicon, rules
from ..detect.pipeline import detect
from ..detect.span import TYPE_LABELS
from ..events.log import REASON_PRIORITY
from ..overlay.source import SITES
from ..sanitize.engine import Session, delete_end, make_items, replacement_for
from ..sanitize.josa import fix_josa
from ..sanitize.restore import restore
from ..server.align import LABEL_CATEGORY, LABEL_TYPE, align, premask, to_orig
from ..server.client import MAX_TEXT, WORK_TTL_SECONDS, ServerError


SERVER_TYPES = {
    "PHONE": "전화번호",
    "EMAIL": "이메일",
    "NAME": "이름",
    "ADDRESS": "주소",
    "ID": "식별번호",
    "ID_NUMBER": "식별번호",
    "ACCOUNT": "계좌번호",
    "CARD": "카드번호",
    "DOB": "생년월일",
    "USERNAME": "사용자명",
    "IP_ADDRESS": "IP 주소",
    "PASSWORD": "비밀번호",
    "API_KEY": "API 키",
    "ACCESS_TOKEN": "토큰",
    "BUSINESS_NUMBER": "사업자번호",
    "SENSITIVE": "민감정보",
    "CONFIDENTIAL": "기밀",
    "INTERNAL": "사내정보",
}
# 원문유지 사유 칩 (이 순서로 보여 준다). 서버에는 오탐 → false_positive, 나머지 → business_need로 간다
KEEP_REASONS = {"business_need": "업무상 필요", "false_positive": "오탐", "approved": "승인된 개인정보"}
# 원문으로 보낼 수 없는 항목: 비밀값, 기밀 표기·사내 소스코드
NO_KEEP_LABELS = {"비밀번호", "API 키", "토큰", "API 키·비밀번호", "기밀 표기", "사내 소스코드", "기밀"}
BLOCK_LABELS = {"기밀 표기", "사내 소스코드"}
_LABEL = re.compile(r"[^\w가-힣 ·]")


class ChipError(ValueError):
    """A message suitable for the chip, with no prompt or credentials."""


@dataclass
class Piece:
    """작업 글(work.text)에서 가릴 수 있는 한 자리. 같은 key는 함께 바뀐다 (같은 값)."""
    start: int
    end: int
    value: str
    repl: str          # 바꿀 글자 ("" = 지우기)
    label: str
    key: str
    keep: bool = True  # 원문유지 가능 (비밀값·기밀은 안 됨)
    server: bool = False  # 서버 자리표시 (서버가 복원)
    kind: str = "other"   # 이 PC 로그에 남길 유형


@dataclass
class ChipWork:
    """한 입력 글에서 계산한 가릴 자리들. applied = 입력창에 이미 바꿔 넣은 key."""
    channel: str
    text: str
    pieces: list
    result: object = None   # 서버 ProcessResult
    server: object = None
    applied: set = field(default_factory=set)
    decided: bool = False
    expires: float = field(default_factory=lambda: time.monotonic() + WORK_TTL_SECONDS)

    def locate(self, applied=None):
        """지금 입력창 글(applied를 바꿔 넣은 글)에서 각 자리의 위치: [(piece, 시작, 끝)]"""
        applied = self.applied if applied is None else applied
        out, delta = [], 0
        for p in self.pieces:
            shown = p.repl if p.key in applied else p.value
            out.append((p, p.start + delta, p.start + delta + len(shown)))
            delta += len(shown) - (p.end - p.start)
        return out

    def render(self, applied=None) -> str:
        applied = self.applied if applied is None else applied
        out, pos = [], 0
        for p in self.pieces:
            out += [self.text[pos:p.start], p.repl if p.key in applied else p.value]
            pos = p.end
        out.append(self.text[pos:])
        return "".join(out)

    def edits(self, keys, apply=True):
        """입력창에서 고칠 자리 [[시작, 끝, 넣을 글자]] (앞에서부터). 뒤에서부터 바꾸면 위치가 밀리지 않는다."""
        return [[a, b, p.repl if apply else p.value] for p, a, b in self.locate()
                if p.key in keys and (p.key in self.applied) != apply]

    def tag_piece(self, start: int, end: int, label: str):
        """서버 결과와 위치가 맞지 않는 하이라이트: 복원되지 않는 [유형] 태그로 가린다."""
        delta = 0
        for p, a, b in self.locate():
            if a < end and start < b:
                return None
            if b <= start:
                delta = b - p.end
        s, e = start - delta, end - delta
        if not (0 <= s < e <= len(self.text)):
            return None
        value = self.text[s:e]
        clean = _LABEL.sub("", label)[:20] or "가림"
        piece = Piece(s, e, value, f"[{clean}]", clean, f"tag:{value}", keep=clean not in NO_KEEP_LABELS)
        self.pieces = sorted(self.pieces + [piece], key=lambda p: p.start)
        return piece


@dataclass
class DraftState:
    """입력창 하나(채널)의 지금 글에 대한 칩 결정. 글을 비우면(보내면) 새로 시작한다."""
    channel: str
    works: list = field(default_factory=list)
    kept: dict = field(default_factory=dict)      # 원래 값 → 사유
    kinds: dict = field(default_factory=dict)     # 원래 값 → 유형 (로그용)
    recorded: set = field(default_factory=set)    # 이미 기록한 원문유지 값
    inserted: set = field(default_factory=set)    # 입력창에 넣은 바꾼 값 (다시 하이라이트하지 않음)
    spans: list = field(default_factory=list)     # 마지막 미리보기 결과
    text: str = ""
    pending: bool = False                         # 기록하지 않은 결정이 있음
    touched: float = field(default_factory=time.monotonic)


def prompt(data, limit=MAX_TEXT):
    text = data.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise ChipError(f"1~{limit:,}자의 글을 입력해 주세요.")
    return text


def _range(text, start, end):
    if not (type(start) is int and type(end) is int and 0 <= start < end <= len(text)):
        raise ChipError("표시된 항목을 다시 눌러 주세요.")
    return start, end


def _occurrences(text, values):
    for value in values:
        if value:
            start = text.find(value)
            while start >= 0:
                yield start, start + len(value)
                start = text.find(value, start + len(value))


def _strongest(reasons):
    return max(reasons, key=lambda r: REASON_PRIORITY.get(r, 0)) if reasons else None


class BrowserActions:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.works = {}       # ticket → ChipWork
        self.drafts = {}      # channel → DraftState
        self.restorable = {}  # channel → [ChipWork] 마지막으로 기록한 입력 (답변 복원용, 서버는 한 번만 복원)
        self.generation = 0
        self.legacy_server = None
        self.preview_retry_at = 0.0
        self.extension = None
        self.session = Session(getattr(app, "base", {}))

    # ------------------------------------------------------------------ 공통
    def _session(self):
        return getattr(self.app, "session", None) or self.session

    def clear(self):
        with self.lock:
            self.generation += 1
            self.works.clear()
            self.drafts.clear()
            self.restorable.clear()
            self.extension = None

    def extension_covers(self, site, text):
        with self.lock:
            lease = self.extension
            return bool(lease and lease[1:3] == (site, hash(text)) and time.monotonic() < lease[3])

    def expire(self):
        with self.lock:
            now = time.monotonic()
            for key in [k for k, w in self.works.items() if w.expires <= now]:
                del self.works[key]
            for channel in [c for c, d in self.drafts.items() if d.touched + WORK_TTL_SECONDS <= now]:
                del self.drafts[channel]
            for channel, works in list(self.restorable.items()):
                alive = [w for w in works if w.expires > now]
                if alive:
                    self.restorable[channel] = alive
                else:
                    del self.restorable[channel]

    def _draft(self, channel) -> DraftState:
        draft = self.drafts.get(channel)
        if draft is None:
            if len(self.drafts) >= 256:
                oldest = min(self.drafts, key=lambda c: self.drafts[c].touched)
                del self.drafts[oldest]
            draft = self.drafts[channel] = DraftState(channel)
        draft.touched = time.monotonic()
        return draft

    def reset(self, channel):
        """입력창을 비웠거나 보낸 뒤: 원문유지·바꾼 값 기억을 지운다 (다음 입력은 다시 묻는다)."""
        with self.lock:
            draft = self.drafts.pop(channel, None)
            if draft:
                for work in draft.works:
                    if not work.decided:
                        for ticket in [t for t, w in self.works.items() if w is work]:
                            del self.works[ticket]
        return {"ok": True}

    def handle(self, data):
        action = data.get("action")
        active = self.app.monitoring_active()
        site = data.get("site")
        channel = data.get("channel")
        if active and site in SITES and isinstance(channel, str):
            with self.lock:
                if action == "preview" and isinstance(data.get("text"), str):
                    self.extension = (channel, site, hash(data["text"]), time.monotonic() + 7)
                elif action == "status" and self.extension and self.extension[:2] == (channel, site):
                    self.extension = (*self.extension[:3], time.monotonic() + 7)
        if action == "status":
            return {"active": active, "mode": "server" if self.app.server_mode() else "local"}
        if not active:
            raise ChipError("감시가 일시정지 또는 꺼짐 상태예요.")
        if not isinstance(channel, str) or not 1 <= len(channel) <= 120:
            raise ChipError("브라우저 연결을 다시 열어 주세요.")
        self.expire()
        if action == "preview":
            return self.preview(prompt(data), channel)
        if action == "mask":
            return self.mask(channel, prompt(data), data.get("start"), data.get("end"),
                             data.get("label") or "", every=data.get("every") is True)
        if action == "commit":
            return self.commit(channel, data.get("ticket"), data.get("keys"), data.get("applied", True) is not False)
        if action == "undo":
            text = data.get("text")
            return self.undo(channel, data.get("ticket"), data.get("keys"), text if isinstance(text, str) else None)
        if action == "keep":
            return self.keep(channel, prompt(data), data.get("start"), data.get("end"), data.get("reason"))
        if action == "unkeep":
            return self.unkeep(channel, prompt(data), data.get("start"), data.get("end"))
        if action == "reset":
            return self.reset(channel)
        if action == "restore":
            return self.restore_reply(channel, prompt(data, 200_000))
        if action in ("files", "settings", "server_tools"):
            self.app.post(action)  # 확장 연결 창에서 직접 연 경우만 (칩에서는 열지 않는다)
            return {"opened": True}
        raise ChipError("지원하지 않는 동작입니다.")

    # ------------------------------------------------------------------ 입력 중 하이라이트
    def preview(self, text, channel=None):
        result = self._detect_spans(text)
        if channel is None:
            return result
        with self.lock:
            draft = self._draft(channel)
            covered = [r for r in _occurrences(text, draft.inserted)]
            spans = []
            for span in result["spans"]:
                value = text[span["start"]:span["end"]]
                if value in draft.kept:
                    continue
                if any(span["start"] < b and a < span["end"] for a, b in covered):
                    continue  # 칩으로 바꿔 넣은 값 ([전화번호_1], 가짜 이름 등)
                spans.append(span)
            draft.spans, draft.text = spans, text
            settle = draft.pending and not spans
            if settle:
                draft.pending = False
        if settle:
            threading.Thread(target=self._settle, args=(draft, text), daemon=True, name="chip-settle").start()
        return {**result, "spans": spans}

    def _detect_spans(self, text):
        app = self.app
        ignore = app.ignore.copy()

        def local_span(s):
            label = TYPE_LABELS.get(s.type, s.type)
            return {"start": s.start, "end": s.end, "label": label,
                    "keep": getattr(s, "grade", "S") != "C" and s.type != "secret"}

        if not app.server_mode():
            result = detect(text, app.base, ignore, model=app.detector())
            return {
                "spans": sorted((local_span(s) for s in result.spans + result.blockers), key=lambda s: s["start"]),
                "coverage": "이 PC에서 검사",
            }
        if not app.server:
            raise ChipError("설정에서 팀 서버에 연결해 주세요.")
        server = app.server
        # Mask local-only organization values and blocked sections before every network request.
        org = lexicon.org_spans(text, app.base, ignore)
        blocked = [
            b
            for b in lexicon.detect_blockers(text, app.base, rules.code_ranges(text))
            if b.value not in ignore
        ]
        private = sorted(blocked + org, key=lambda s: (s.start, -s.length))
        protected = []
        for span in private:
            if protected and span.start < protected[-1][1]:
                protected[-1] = (protected[-1][0], max(span.end, protected[-1][1]))
            else:
                protected.append((span.start, span.end))
        sent, repls = premask(text, [(a, b, "[보호]", "local", text[a:b]) for a, b in protected])
        spans = [local_span(s) for s in private]
        legacy = self.legacy_server is server and time.monotonic() < self.preview_retry_at
        if not legacy:
            try:
                remote, limited = server.preview(sent)
            except ServerError as exc:
                if exc.status != 404:
                    raise
                legacy = True
                self.legacy_server = server
                self.preview_retry_at = time.monotonic() + 60
        missing = 0
        if legacy:
            # Older servers only expose masked text. Never guess ambiguous ranges.
            result = server.process(sent)
            remote = [
                {"start": it.start, "end": it.end, "type": it.label}
                for it in align(sent, result.masked_text)
                if it.exact
            ]
            limited = result.rules_only
            missing = max(0, result.detected_count - len(remote))
        for item in remote:
            start, end = item["start"], item["end"]
            if any(start < r.s_end and end > r.s_start for r in repls):
                continue
            start, end = to_orig(start, repls), to_orig(end, repls)
            if start is not None and end is not None and text[start:end] not in ignore:
                label = SERVER_TYPES.get(item["type"], item["type"])
                spans.append({"start": start, "end": end, "label": label, "keep": label not in NO_KEEP_LABELS})
        coverage = "서버 규칙 검사" if limited else "팀 서버 검사"
        return {
            "spans": sorted(spans, key=lambda s: (s["start"], s["end"])),
            "coverage": coverage,
            "unlocated": missing,
        }

    # ------------------------------------------------------------------ 마스킹
    def _new_work(self, channel, text) -> ChipWork:
        """이 글에서 가릴 자리와 바꿀 값을 계산한다. 서버 처리면 /process로 자리표시를 받는다 (네트워크, 잠금 밖)."""
        app = self.app
        ignore = app.ignore.copy()
        session = self._session()
        if not app.server_mode():
            detection = detect(text, app.base, ignore, model=app.detector())
            pieces, cursor = [], 0
            for it in sorted(make_items(detection, {}), key=lambda it: (it.span.start, -it.span.length)):
                s = it.span
                if s.start < cursor:
                    continue
                if it.is_blocker:
                    end, repl = s.end, ""
                elif it.strategy == "delete":
                    end, repl = delete_end(text, s), ""
                else:
                    repl, end = replacement_for(it, session, text), s.end
                    fixed, n = fix_josa(repl, text, end)
                    if n:
                        repl, end = repl + fixed, end + n
                pieces.append(Piece(s.start, end, text[s.start:end], repl, TYPE_LABELS.get(s.type, s.type),
                                    f"{s.type}:{s.value}", keep=not it.is_blocker and s.type != "secret",
                                    kind=s.type))
                cursor = end
            return ChipWork(channel, text, pieces)
        server = app.server
        if server is None:
            raise ChipError("설정에서 팀 서버에 연결해 주세요.")
        org = lexicon.org_spans(text, app.base, ignore)
        blocked = [b for b in lexicon.detect_blockers(text, app.base, rules.code_ranges(text)) if b.value not in ignore]
        local = [(s.start, s.end, session.tag_for(s.type, s.value), "org", s.value) for s in org]
        local += [(b.start, b.end, "[보호]", "block", b.value) for b in blocked]
        sent, repls = premask(text, local)
        result = server.process(sent)
        org_types = {(s.start, s.end): s.type for s in org}
        pieces = []
        for r in repls:
            if r.kind == "org":
                typ = org_types.get((r.o_start, r.o_end), "codename")
                pieces.append(Piece(r.o_start, r.o_end, r.value, r.tag, TYPE_LABELS.get(typ, typ), f"org:{r.value}",
                                    kind=typ))
            else:
                pieces.append(Piece(r.o_start, r.o_end, r.value, "", "기밀", f"block:{r.o_start}", keep=False,
                                    kind="classified"))
        for it in align(sent, result.masked_text):
            if not it.exact or any(it.start < r.s_end and it.end > r.s_start for r in repls):
                continue
            start, end = to_orig(it.start, repls), to_orig(it.end, repls)
            if start is None or end is None or text[start:end] in ignore:
                continue
            secret = LABEL_CATEGORY.get(it.label) == "secret"
            pieces.append(Piece(start, end, text[start:end], it.token, SERVER_TYPES.get(it.label, it.label),
                                it.token, keep=not secret, server=True, kind=LABEL_TYPE.get(it.label, "other")))
        pieces.sort(key=lambda p: (p.start, p.start - p.end))
        clean, cursor = [], 0
        for p in pieces:
            if p.start >= cursor:
                clean.append(p)
                cursor = p.end
        return ChipWork(channel, text, clean, result, server)

    def mask(self, channel, text, start, end, label="", every=False):
        """누른 하이라이트를 가렸을 때의 값과 입력창에서 고칠 자리를 돌려준다. 아직 기록하지 않는다 (적용하면 commit)."""
        start, end = _range(text, start, end)
        with self.lock:
            draft = self._draft(channel)
            work = next((w for w in reversed(draft.works) if not w.decided and w.render() == text), None)
            generation = self.generation
        if work is None:
            work = self._new_work(channel, text)
            with self.lock:
                if self.generation != generation:
                    raise ChipError("처리 설정이 바뀌었어요. 다시 눌러 주세요.")
                draft = self._draft(channel)
                draft.works.append(work)
        with self.lock:
            hits = [(min(b, end) - max(a, start), p) for p, a, b in work.locate() if a < end and start < b]
            piece = max(hits, key=lambda h: h[0])[1] if hits else work.tag_piece(start, end, label or "가림")
            if piece is None:
                raise ChipError("이 항목의 위치를 확정하지 못했어요. 다시 눌러 주세요.")
            if every:
                kept = self._draft(channel).kept
                keys = {p.key for p in work.pieces if p.key not in work.applied and p.value not in kept}
                keys.add(piece.key)
            else:
                keys = {piece.key}
            ticket = next((t for t, w in self.works.items() if w is work), None)
            if ticket is None:
                if len(self.works) >= 256:
                    raise ChipError("열린 칩이 너무 많아요. 잠시 뒤 다시 눌러 주세요.")
                ticket = secrets.token_urlsafe(24)
                self.works[ticket] = work
            edits = work.edits(keys)
        return {
            "ticket": ticket,
            "keys": sorted(keys),
            "word": piece.repl,
            "deleted": piece.repl == "",
            "preview": _hide(piece.value),
            "label": piece.label,
            "edits": edits,
            "count": len(edits),
        }

    def _work(self, channel, ticket, decided=False) -> ChipWork:
        """decided=True: 기록한 뒤에도 되돌리기는 받는다 (다시 기록한다)."""
        work = self.works.get(ticket) if isinstance(ticket, str) else None
        if work is None or work.channel != channel or (work.decided and not decided):
            raise ChipError("칩이 만료됐어요. 하이라이트를 다시 눌러 주세요.")
        return work

    def commit(self, channel, ticket, keys, applied=True):
        """입력창에서 바꿨다(applied) / 되돌렸다. 남은 하이라이트가 없어질 때 한 번 기록한다."""
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ChipError("고른 항목을 다시 확인해 주세요.")
        with self.lock:
            work = self._work(channel, ticket, decided=not applied)
            known = {p.key for p in work.pieces}
            keys = [k for k in keys if k in known]
            if applied:
                work.applied.update(keys)
            else:
                work.applied.difference_update(keys)
                if work.decided:  # 기록한 뒤 되돌림: 다음에 남은 하이라이트가 없어지면 다시 기록한다
                    work.decided = False
                    if work in self.restorable.get(channel, []):
                        self.restorable[channel].remove(work)
            draft = self._draft(channel)
            draft.inserted = {p.repl for w in draft.works for p in w.pieces if p.key in w.applied and p.repl}
            draft.pending = True
        return {"ok": True}

    def undo(self, channel, ticket, keys, text=None):
        """적용을 되돌릴 자리 (입력창에서 바꾼 값 → 원래 값). 입력창이 바꾼 뒤 commit(applied=False).
        text: 지금 입력창 글. 그 사이 다른 곳을 고쳤으면 자리가 맞지 않아 되돌리지 않는다."""
        if not isinstance(keys, list):
            raise ChipError("고른 항목을 다시 확인해 주세요.")
        with self.lock:
            work = self._work(channel, ticket, decided=True)
            if text is not None and work.render() != text:
                raise ChipError("그 사이 입력이 바뀌어 되돌릴 수 없어요. 직접 고쳐 주세요.")
            return {"ticket": ticket, "keys": keys, "edits": work.edits(set(keys), apply=False)}

    # ------------------------------------------------------------------ 원문유지
    def keep(self, channel, text, start, end, reason):
        if reason not in KEEP_REASONS:
            raise ChipError("원문을 유지하는 사유를 골라 주세요.")
        start, end = _range(text, start, end)
        value = text[start:end]
        with self.lock:
            draft = self._draft(channel)
            if draft.text != text:
                raise ChipError("입력이 바뀌었어요. 하이라이트를 다시 눌러 주세요.")
            span = next((s for s in draft.spans if s["start"] < end and start < s["end"]), None)
            if span is None:
                raise ChipError("이 항목은 이미 처리됐어요.")
            if span.get("keep") is False:
                raise ChipError("비밀값·기밀은 원문으로 보낼 수 없어요. 마스킹을 골라 주세요.")
            value = text[span["start"]:span["end"]]
            draft.kept[value] = reason
            draft.kinds[value] = _kind(span["label"])
            draft.pending = True
            # 글은 그대로라 다시 검사하지 않는다: 남은 하이라이트가 없으면 지금 기록한다
            draft.spans = [s for s in draft.spans if text[s["start"]:s["end"]] != value]
            settle = not draft.spans
            if settle:
                draft.pending = False
        if reason == "false_positive":
            self.app.ignore.add(value)  # 이 값은 앞으로 탐지하지 않는다 (메모리, 앱을 끄면 사라짐)
        if settle:
            threading.Thread(target=self._settle, args=(draft, text), daemon=True, name="chip-settle").start()
        return {"ok": True, "reason": KEEP_REASONS[reason], "left": len(draft.spans)}

    def unkeep(self, channel, text, start, end):
        """원문유지를 되돌린다: 다음 검사부터 다시 하이라이트한다."""
        start, end = _range(text, start, end)
        value = text[start:end]
        with self.lock:
            draft = self._draft(channel)
            reason = draft.kept.pop(value, None)
            draft.kinds.pop(value, None)
            draft.recorded.discard(value)
        if reason == "false_positive":
            self.app.ignore.discard(value)
        return {"ok": reason is not None}

    # ------------------------------------------------------------------ 기록·복원
    def _settle(self, draft: DraftState, text: str):
        """남은 하이라이트가 없다: 이 입력의 결정을 기록한다. 서버 작업마다 /process/decide 한 번."""
        app = self.app
        with self.lock:
            works = [w for w in draft.works if not w.decided]
            kept = {v: r for v, r in draft.kept.items() if v not in draft.recorded}
            kinds = dict(draft.kinds)
            draft.recorded.update(kept)
            for w in works:
                w.decided = True
        masked = [p.kind for w in works for p in w.pieces if p.key in w.applied]
        if not masked and not kept:
            return
        reason = _strongest(list(kept.values()))
        restorable, failed = [], None
        if app.server_mode():
            server_works = [w for w in works if w.result is not None]
            if not server_works and kept and app.server is not None:
                try:  # 원문유지만 고른 입력: 서버에 기록할 작업을 만든다
                    server_works = [self._new_work(draft.channel, text)]
                except (ServerError, ChipError) as exc:
                    failed = str(exc)
                except Exception:  # 예외 메시지에 원문이 섞일 수 있어 보여 주지 않는다
                    failed = "서버에 기록하지 못했어요."
            for w in server_works:
                action = "send_transformed" if any(p.server and p.key in w.applied for p in w.pieces) else "send_as_is"
                try:
                    out = w.server.decide(w.result.work_id, action, reason)
                except ServerError as exc:
                    failed = exc.message
                    continue
                except Exception:
                    failed = "서버에 기록하지 못했어요."
                    continue
                if out.restore_available:
                    restorable.append(w)
        else:
            restorable = [w for w in works if w.applied]
        with self.lock:
            if restorable:
                self.restorable[draft.channel] = restorable
        post = getattr(app, "post", None)
        if post is None:
            return
        if restorable and app.server_mode():
            last = restorable[-1]
            tokens = frozenset(p.repl for p in last.pieces if p.server and p.key in last.applied)
            post("chip_restore", last.result, tokens)
        log = getattr(app, "log", None)
        if log is not None:
            kept_kinds = [kinds.get(v, "other") for v in kept]
            types = masked + kept_kinds
            post("call", lambda: log.record(
                service="chip", grade="S", action="sent_original" if kept and not masked else "sent_sanitized",
                detect_types=types, counts=dict(Counter(types)), reverted_types=kept_kinds,
                reasons=list(kept.values()), max_confidence=0.9))
        if failed:
            post("server_decided", None, failed)

    def restore_reply(self, channel, reply):
        """AI 답변의 바꾼 값을 원래 값으로. 서버 작업은 한 번만 복원된다."""
        with self.lock:
            works = self.restorable.pop(channel, [])
        if not works and self.app.server_mode():
            raise ChipError("복원할 답변이 없어요. 마스킹을 적용해 보낸 뒤 10분 안에만 복원할 수 있어요.")
        text, blocked, unknown = reply, [], []
        for work in reversed(works):
            if work.result is not None:
                result = work.server.restore(work.result.work_id, text)
                text = result.text
                blocked += [t for t in result.blocked if t not in blocked]
                unknown = [t for t in result.unknown if t not in blocked]
        value, _ = restore(text, self._session().entries)
        return {"text": value, "blocked": blocked, "unknown": unknown}


_LOCAL_TYPES = {label: typ for typ, label in TYPE_LABELS.items()}


def _kind(label: str) -> str:
    """하이라이트 이름 → 이 PC 로그의 유형 이름."""
    return _LOCAL_TYPES.get(label) or LABEL_TYPE.get(label.replace(" ", "")) or "other"


def _hide(value: str) -> str:
    """지운 값을 칩에 보일 때: 앞뒤 일부만 남기고 가린다."""
    chars = [i for i, c in enumerate(value) if c.isalnum()]
    if len(chars) <= 1:
        return "*" * len(value) if chars else value
    keep = max(1, len(chars) // 4)
    hidden = set(chars[keep:-keep]) if len(chars) > 2 else {chars[-1]}
    return "".join("*" if i in hidden else c for i, c in enumerate(value))
