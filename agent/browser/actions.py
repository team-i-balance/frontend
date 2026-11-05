"""Shared live ranges, review and restore operations for paired editors."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

from ..detect import lexicon, rules
from ..detect.pipeline import detect
from ..detect.span import TYPE_LABELS
from ..sanitize.engine import Session, make_items, sanitize
from ..sanitize.restore import restore
from ..server.align import LABEL_CATEGORY, align, compose, premask, to_orig
from ..server.client import MAX_TEXT, REASONS, WORK_TTL_SECONDS, ServerError


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


class ChipError(ValueError):
    """A message suitable for the chip, with no prompt or credentials."""


@dataclass
class Work:
    channel: str
    original: str
    session: Session
    server: object
    masked: str
    result: object = None
    choices: dict = field(default_factory=dict)
    items: list = field(default_factory=list)
    local_detection: object = None
    state: str = "review"
    expires: float = field(default_factory=lambda: time.monotonic() + WORK_TTL_SECONDS)

    def rendered(self, keep):
        if not isinstance(keep, list) or not all(isinstance(k, str) and k in self.choices for k in keep):
            raise ChipError("선택한 항목을 다시 확인해 주세요.")
        if not keep:
            return self.masked
        if self.server:
            return "".join(piece for piece, _ in compose(self.masked, self.items, set(keep)))
        decisions = {self.choices[k]: {"applied": False} for k in keep}
        return sanitize(self.original, make_items(self.local_detection, decisions), self.session).text


def prompt(data, limit=MAX_TEXT):
    text = data.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > limit:
        raise ChipError(f"1~{limit:,}자의 글을 입력해 주세요.")
    return text


class BrowserActions:
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.works = {}
        self.generation = 0
        self.legacy_server = None
        self.preview_retry_at = 0.0

    def clear(self):
        with self.lock:
            self.generation += 1
            self.works.clear()

    def expire(self):
        with self.lock:
            now = time.monotonic()
            for key in [k for k, w in self.works.items() if w.expires <= now]:
                del self.works[key]

    def handle(self, data):
        action = data.get("action")
        active = self.app.monitoring_active()
        if action == "status":
            return {"active": active, "mode": "server" if self.app.server_mode() else "local"}
        if not active:
            raise ChipError("KP Guard 감시가 일시정지 또는 꺼짐 상태입니다.")
        channel = data.get("channel")
        if not isinstance(channel, str) or not 1 <= len(channel) <= 120:
            raise ChipError("브라우저 연결을 다시 열어 주세요.")
        self.expire()
        if action == "preview":
            return self.preview(prompt(data))
        if action == "inspect":
            return self.inspect(channel, prompt(data))
        if action in ("selection", "decide", "restore"):
            return self.finish(channel, action, data)
        if action == "detail":
            self.app.post("browser_detail", prompt(data))
        elif action in ("files", "settings", "server_tools"):
            self.app.post(action)
        elif action == "desktop_restore":
            self.app.post("browser_restore", prompt(data) if data.get("text") else "")
        else:
            raise ChipError("지원하지 않는 동작입니다.")
        return {"opened": True}

    def preview(self, text):
        app = self.app
        ignore = app.ignore.copy()

        def local_span(s):
            return {"start": s.start, "end": s.end, "label": TYPE_LABELS.get(s.type, s.type)}

        if not app.server_mode():
            result = detect(text, app.base, ignore, model=app.detector())
            return {
                "spans": [local_span(s) for s in result.spans + result.blockers],
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
                spans.append(
                    {"start": start, "end": end, "label": SERVER_TYPES.get(item["type"], item["type"])}
                )
        coverage = "서버 규칙 검사" if limited else "팀 서버 검사"
        return {
            "spans": sorted(spans, key=lambda s: (s["start"], s["end"])),
            "coverage": coverage,
            "unlocated": missing,
        }

    def inspect(self, channel, text):
        app = self.app
        generation = self.generation
        server_mode = app.server_mode()
        server = app.server if server_mode else None
        if server_mode and server is None:
            raise ChipError("KP Guard 설정에서 서버 연결을 확인해 주세요.")
        ignore = app.ignore.copy()
        detection = detect(text, app.base, ignore, model=None if server_mode else app.detector())
        if detection.blockers:
            raise ChipError("기밀 항목이 있어 검사를 멈췄습니다. 상세 점검에서 해당 부분을 확인해 주세요.")
        session = Session(app.base)
        work = Work(channel, text, session, server, "")
        if server_mode:
            org = lexicon.org_spans(text, app.base, ignore)
            sent, replacements = premask(
                text, [(s.start, s.end, session.tag_for(s.type, s.value), "org", s.value) for s in org]
            )
            result = server.process(sent)
            work.result, work.masked = result, result.masked_text
            work.items = align(sent, result.masked_text)
            for item in work.items:
                # Only unambiguously aligned non-secret values can be retained inline.
                # Pre-masked organization tags always stay protected in the browser.
                if (
                    item.exact
                    and item.value
                    and item.value not in ignore
                    and LABEL_CATEGORY.get(item.label) != "secret"
                    and not any(item.start < r.s_end and item.end > r.s_start for r in replacements)
                ):
                    work.choices[item.token] = item.token
            coverage = "서버 규칙 검사" if result.rules_only else "서버 검사"
            remaining = result.remaining_count
        else:
            work.local_detection = detection
            items = make_items(detection, {})
            work.masked = sanitize(text, items, session).text
            for i, item in enumerate(items):
                if item.applied and item.span.type != "secret":
                    work.choices[f"{TYPE_LABELS.get(item.span.type, item.span.type)} {i + 1}"] = item.span.key
            coverage = "이 PC · 모델 포함" if detection.coverage == "model" else "이 PC · 규칙과 사전"
            remaining = sum(item.suspect for item in items)
        ticket = secrets.token_urlsafe(24)
        with self.lock:
            if len(self.works) >= 128:
                raise ChipError("열린 점검이 많습니다. 이전 점검을 닫고 다시 시도해 주세요.")
            if (
                self.generation != generation
                or not app.monitoring_active()
                or app.server_mode() != server_mode
                or (server_mode and app.server is not server)
            ):
                raise ChipError("처리 설정이 변경되었습니다. 다시 검사해 주세요.")
            self.works[ticket] = work
        return {
            "ticket": ticket,
            "text": work.masked,
            "choices": list(work.choices),
            "coverage": coverage,
            "remaining": remaining,
        }

    def finish(self, channel, operation, data):
        ticket = data.get("ticket")
        if not isinstance(ticket, str):
            raise ChipError("점검을 다시 시작해 주세요.")
        wanted = "applied" if operation == "restore" else "review"
        with self.lock:
            work = self.works.get(ticket)
            if (
                not work
                or work.channel != channel
                or work.state != wanted
                or work.server is not (self.app.server if self.app.server_mode() else None)
            ):
                raise ChipError("점검이 만료되었거나 이미 처리되었습니다.")
            if operation == "selection":
                return {"text": work.rendered(data.get("keep", []))}
            work.state = "busy"
        next_state = "done"
        try:
            if operation == "restore":
                reply = prompt(data, 200_000)
                result = work.server.restore(work.result.work_id, reply) if work.server else None
                value, _ = restore(result.text if result else reply, work.session.entries)
                return {
                    "text": value,
                    "blocked": result.blocked if result else [],
                    "unknown": result.unknown if result else [],
                }
            choice = data.get("choice")
            keep = data.get("keep", [])
            if choice not in ("apply", "original", "cancel"):
                raise ChipError("선택한 항목을 다시 확인해 주세요.")
            reason = data.get("reason")
            if (choice == "original" or keep) and (not isinstance(reason, str) or reason not in REASONS):
                raise ChipError("원문을 유지하는 사유를 선택해 주세요.")
            action = {"apply": "send_transformed", "original": "send_as_is", "cancel": "cancel"}[choice]
            value = work.rendered(keep) if choice == "apply" else work.original
            if choice == "apply" and value == work.original:
                action = "send_as_is"
            if work.server:
                decided = work.server.decide(work.result.work_id, action, reason)
                can_restore = decided.restore_available
            else:
                can_restore = bool(work.session.entries)
            if choice == "apply" and can_restore:
                next_state = "applied"
            return {"text": value if choice != "cancel" else None, "canRestore": next_state == "applied"}
        except ChipError:
            next_state = wanted
            raise
        except ServerError as exc:
            # Never replay an ambiguous decision / one-time restore automatically.
            if exc.kind in ("unauthorized", "forbidden", "untrusted_cert", "pin_mismatch", "insecure", "tls"):
                next_state = wanted
            raise
        finally:
            with self.lock:
                if next_state == "done":
                    self.works.pop(ticket, None)
                else:
                    work.state = next_state
