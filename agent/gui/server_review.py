"""서버 처리 점검 창과 답변 복원 창.

탐지·가명화는 팀 서버(/process)가 하고, 사용자의 선택은 서버(/process/decide)에 기록하고,
AI 답변 복원도 서버(/process/restore)가 한다. 이 PC에서 하는 일은 서버 API에 없는 것만이다.
- 서버 결과를 원문과 맞춰 하이라이트하고, 항목별 되돌리기를 보낼 글에 반영
- 조직 베이스(코드네임·고객사·사내 도메인)와 기밀 표기·사내 소스코드 확인: 서버에 조직 베이스가 아직 없어서
- 놓친 정보 신고: 보낼 글에서 한 번 더 가림
서버 요청은 작업 스레드에서 하고, tkinter는 메인 스레드에서만 다룬다.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections import Counter
from urllib.parse import urlsplit

from ..detect import lexicon, rules
from ..detect.span import TYPE_LABELS
from ..events.log import REASON_PRIORITY
from ..sanitize.restore import restore as local_restore
from ..server.align import LABEL_TYPE, PLACEHOLDER, Repl, align, compose, premask, to_orig
from ..server.client import AUTH_KINDS, CERT_KINDS, NOT_PROCESSED, ServerError
from ..system import platform_api
from . import theme
from .restore_window import build_restore_layout
from .review import MISSED_TYPES, REASON_SHORT, REASONS, ReasonDialog, Tooltip, _short, build_layout, shape_of

SERVER_COLUMNS = (("type", "유형", 120), ("orig", "원문", 180), ("repl", "보낼 값", 170),
                  ("state", "상태", 190), ("why", "탐지", 330))


def _host(app) -> str:
    return urlsplit(app.server.base_url).netloc if app.server else "서버"


class ServerReviewWindow(tk.Toplevel):
    def __init__(self, app, text: str = "", source: str = "manual", selected=None):
        super().__init__(app.root)
        self.app = app
        self.source = source
        self.overlay_selection = selected
        self.result = None            # ProcessResult
        self.result_text = ""         # 결과를 받은 원문
        self.items = []               # align() 결과
        self.error: ServerError | None = None
        self.loading = False
        self.reverted: dict[str, str] = {}       # 자리표시 → 사유 (원문 그대로 보냄)
        self.org_reverted: dict[str, str] = {}   # 조직 베이스 값 → 사유
        self.released: dict[tuple, str] = {}     # 기밀 항목 키 → 차단 해제 사유
        self.manual: list[tuple[str, str]] = []  # (유형, 값) 놓친 정보 신고
        self.blockers = []
        self.org = []                 # 조직 베이스 값이 나온 모든 자리 (서버로 보내기 전에 모두 가린다)
        self.repls: list[Repl] = []   # 서버로 보내기 전에 이 PC에서 태그로 바꾼 구간 (마지막 결과 기준)
        self.result_sent = ""          # 마지막 결과를 받을 때 서버로 보낸 글
        self.segs = []
        self.outgoing = ""
        self._gen = 0
        self._q: queue.Queue = queue.Queue()
        self._job = None
        self._pending_click = None
        self.tip = Tooltip(self)

        theme.window(self, "AI 전송 전 점검 (서버) — KP Guard", 1200, 840, 900, 640)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self._build()
        if text:
            self.orig.insert("1.0", text)
        self.orig.edit_modified(False)
        self.check()
        self._bring_front()
        self.after(100, self._poll)

    # ================================================================== UI
    def _build(self) -> None:
        self.btn_send = None
        build_layout(self, "AI에 보낼 변환문 (서버가 가린 글)", SERVER_COLUMNS)
        self.btn_retry = theme.Button(self.b_actions, "다시 시도", self.check, size="sm")
        self.btn_local = theme.Button(self.b_actions, "이 PC에서 검사", self.on_local, size="sm")
        # 인증서·토큰 문제는 설정 창의 '인증·인증서'에서 고친다
        self.btn_settings = theme.Button(self.b_actions, "설정 열기", lambda: self.app.post("settings"), size="sm")
        self.btn_internal = theme.Button(self.b_actions, "사내 AI에서 처리", self.on_internal, size="sm")
        self.orig.bind("<<Modified>>", self._on_modified)
        self.orig.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        self.btn_send = theme.Button(self.bottom, "변환문 복사", self.on_send, kind="primary")
        self.btn_send.pack(side="right")
        theme.Button(self.bottom, "취소", self.on_cancel).pack(side="right", padx=theme.px(8))
        theme.Button(self.bottom, "원문 그대로 사용", self.on_original, kind="ghost").pack(side="right")
        self.bind("<Control-Return>", lambda e: self.on_send())
        self.bind("<Escape>", lambda e: self.on_cancel())

    def _bring_front(self) -> None:
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, lambda: self.attributes("-topmost", False))
        self.focus_force()
        self.orig.focus_set()

    # ================================================================== 서버 검사
    def _on_modified(self, _event=None) -> None:
        if not self.orig.edit_modified():
            return
        self.orig.edit_modified(False)
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(700, self.check)  # 입력이 멈추면 서버에 다시 묻는다

    def _text(self) -> str:
        return self.orig.get("1.0", "end-1c")

    def check(self) -> None:
        self._job = None
        text = self._text()
        self._local_checks(text)
        self._gen += 1
        gen = self._gen
        if not text.strip() or self.app.server is None:
            self.loading, self.result, self.items, self.result_text, self.repls = False, None, [], text, []
            self.error = None if not text.strip() else ServerError("서버 주소가 설정돼 있지 않아요. 설정 창에서 지정하세요.",
                                                                   "unreachable")
            self.render()
            return
        sent, repls = self._premask(text)
        self.loading, self.error = True, None
        self.render()
        server = self.app.server

        def work():
            try:
                res, err = server.process(sent), None
            except ServerError as exc:
                res, err = None, exc
            except Exception:  # 예외 메시지에 원문이 섞일 수 있어 보여 주지 않는다
                res, err = None, ServerError("서버 요청 중 오류가 났어요.")
            self._q.put((gen, text, sent, repls, res, err))

        threading.Thread(target=work, daemon=True, name="server-process").start()

    def _premask(self, text: str):
        """조직 베이스 값과 직접 신고한 값은 서버로 보내기 전에 이 PC에서 태그로 바꾼다.

        서버가 그 값의 일부만 가려 조각이 새는 일을 막고, 회사 기밀 이름은 서버에도 보내지 않는다.
        """
        spans = [(s.start, s.end, self.app.session.tag_for(s.type, s.value), "org", s.value)
                 for s in self.org if s.value not in self.org_reverted]
        for _, value in self.manual:
            start = text.find(value)
            while start >= 0:
                spans.append((start, start + len(value), "[가림]", "manual", value))
                start = text.find(value, start + len(value))
        return premask(text, spans)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                gen, text, sent, repls, res, err = self._q.get_nowait()
                if gen != self._gen:
                    continue  # 그 뒤에 원문이 바뀌어 다시 물었음
                self.loading, self.result, self.error, self.result_text = False, res, err, text
                self.result_sent, self.repls = sent, repls
                items = align(sent, res.masked_text) if res else []
                for it in items:  # 위치를 원문 기준으로 바꾼다 (값은 보낸 글 기준 그대로)
                    start, end = to_orig(it.start, repls), to_orig(it.end, repls)
                    if it.exact and start is not None and end is not None:
                        it.start, it.end = start, end
                    elif it.exact:
                        it.start = it.end = -1
                        it.exact = False
                self.items = items
                exact = {it.token: it.value for it in self.items if it.exact}
                self.reverted = {t: r for t, r in self.reverted.items() if t in exact}
                for token, value in exact.items():  # 오탐으로 등록한 값은 서버가 가려도 되돌린다
                    if value in self.app.ignore:
                        self.reverted.setdefault(token, "false_positive")
                self.render()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _local_checks(self, text: str) -> None:
        """서버에 없는 것: 조직 베이스(코드네임·고객사·사내 도메인), 기밀 표기·사내 소스코드."""
        ignore = self.app.ignore
        cr = rules.code_ranges(text)
        self.blockers = [b for b in lexicon.detect_blockers(text, self.app.base, cr) if b.value not in ignore]
        self.org = lexicon.org_spans(text, self.app.base, ignore)

    def _org_values(self):
        """조직 베이스 값마다 처음 나온 자리 하나 (표·통계용)."""
        first = {}
        for s in self.org:
            first.setdefault(s.value, s)
        return list(first.values())

    # ================================================================== 상태
    def _active_blockers(self):
        return [b for b in self.blockers if b.key not in self.released]

    def grade(self) -> str | None:
        """None: 아직 결과 없음 / C: 기밀 차단(이 PC 조직 베이스) / C2: 비밀값을 가림(서버) / S / O"""
        if self._active_blockers():
            return "C"
        if self.result is None:
            return None
        if self.result.grade == "C":
            return "C2"
        if self.result.grade == "S" or self.items or self.repls:
            return "S"
        return "O"

    def _extra(self):
        """이 PC에서 미리 바꾼 태그는 서버 결과에 글자 그대로 들어 있다 → 하이라이트만 한다."""
        return [(r.tag, r.tag, r.kind) for r in self.repls]

    def _fresh(self) -> bool:
        return self.result is not None and self.result_text == self._text()

    # ================================================================== 그리기
    def render(self) -> None:
        if self.result is not None:
            self.segs = compose(self.result.masked_text, self.items, set(self.reverted), self._extra())
        else:
            self.segs = []
        self.outgoing = "".join(s for s, _ in self.segs)
        self._paint_original()
        self._paint_output()
        self._fill_table()
        self._update_banner()

    def _bind_mark(self, widget: tk.Text, tag: str, key) -> None:
        widget.tag_bind(tag, "<ButtonRelease-1>", lambda e, k=key, w=widget: self._on_tag_click(e, k, w))
        widget.tag_bind(tag, "<Enter>", lambda e, k=key: self._show_tip(e, k))
        widget.tag_bind(tag, "<Leave>", lambda e: self.tip.hide())

    @staticmethod
    def _clear_marks(widget: tk.Text) -> None:
        for tag in widget.tag_names():
            if tag.startswith("k"):
                widget.tag_delete(tag)

    def _paint_original(self) -> None:
        self._clear_marks(self.orig)
        text = self._text()
        marks = []
        mark = {**theme.MARK, "font": theme.F["mark16"]}  # 가린 원문 표시
        if self._fresh():
            for it in self.items:
                if it.exact:
                    style = dict(theme.KEPT) if it.token in self.reverted else mark
                    marks.append((it.start, it.end, style, ("t", it.token)))
        for s in self.org:
            style = dict(theme.KEPT) if s.value in self.org_reverted else mark
            marks.append((s.start, s.end, style, ("o", s.value)))
        for i, (_, value) in enumerate(self.manual):
            start = text.find(value)
            while start >= 0:
                marks.append((start, start + len(value), mark, ("m", i)))
                start = text.find(value, start + len(value))
        for i, b in enumerate(self.blockers):
            style = dict(theme.KEPT) if b.key in self.released else {**theme.BLOCKED, "font": theme.F["mark16"]}
            marks.append((b.start, b.end, style, ("b", i)))
        for n, (s, e, style, key) in enumerate(marks):
            tag = f"k{n}"
            self.orig.tag_add(tag, theme.index(s), theme.index(e))
            self.orig.tag_configure(tag, **style)
            self._bind_mark(self.orig, tag, key)
            if self.overlay_selection == (s, e):
                self.overlay_selection = None
                self.after_idle(lambda target=key, start=s: theme.open_at_mark(self, start, target))
            if key[0] == "b":
                self.orig.tag_lower(tag)
        self.orig.tag_raise("sel")

    def _paint_output(self) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self._clear_marks(self.out)
        for n, (chunk, mark) in enumerate(self.segs):
            if mark is None:
                self.out.insert("end", chunk)
                continue
            tag = f"k{n}"
            kind, ref = mark
            if kind == "token":
                style, key = dict(theme.SAFE), ("t", ref)
            elif kind == "reverted":
                style, key = dict(theme.RAW), ("t", ref)  # 원문 그대로 나감
            else:
                style, key = dict(theme.SAFE), None
            self.out.insert("end", chunk, (tag,))
            self.out.tag_configure(tag, **style)
            if key:
                self._bind_mark(self.out, tag, key)
        self.out.configure(state="disabled")
        masked = sum(1 for _, m in self.segs if m and m[0] in ("token", "extra"))
        self.out_info.configure(text=f"가린 곳 {masked}곳" if masked else "")
        self.cov_info.set(*self._coverage_label())

    def _coverage_label(self) -> tuple[str, str]:
        insecure = " · HTTP" if self.app.server and self.app.server.insecure else ""
        if self.loading:
            return f"서버에서 검사 중{insecure}", "neutral"
        if self.result is None:
            return ("서버 연결 안 됨", "danger") if self.error else ("", "neutral")
        if self.result.rules_only or self.result.confidence != "high":
            return f"서버 규칙만 · 이름·주소 미검사{insecure}", "warning"
        return f"검사: 서버 모델+규칙{insecure}", "warning" if insecure else "neutral"

    def _rows(self):
        """(키, 유형, 원문, 보낼 값, 상태, 탐지 근거, 표 태그)"""
        rows, seen = [], set()
        for it in self.items:
            if it.token in seen:
                continue
            seen.add(it.token)
            if not it.exact:
                state, value, tag = "위치 불명 · 되돌릴 수 없음", "(원문 위치를 확정하지 못함)", ""
            elif it.token in self.reverted:
                state, value, tag = f"되돌림 · {REASON_SHORT[self.reverted[it.token]]}", theme.masked(it.value), "muted"
            else:
                state, value, tag = "적용", theme.masked(it.value), ""
            repl = "(원문 유지)" if it.token in self.reverted else it.token
            why = "서버 탐지 · " + ("규칙" if self.result and self.result.rules_only else "모델+규칙")
            rows.append((("t", it.token), it.label, value, repl, state, why, tag))
        for s in self._org_values():
            reverted = s.value in self.org_reverted
            rows.append((("o", s.value), TYPE_LABELS[s.type], theme.masked(s.value),
                         "(원문 유지)" if reverted else self.app.session.tag_for(s.type, s.value),
                         f"되돌림 · {REASON_SHORT[self.org_reverted[s.value]]}" if reverted else "적용 (서버로 안 보냄)",
                         s.reason, "muted" if reverted else ""))
        for i, (typ, value) in enumerate(self.manual):
            rows.append((("m", i), TYPE_LABELS.get(typ, "기타"), theme.masked(value), "[가림]", "적용 (직접 신고)",
                         "사용자가 직접 지정", ""))
        for i, b in enumerate(self.blockers):
            released = b.key in self.released
            rows.append((("b", i), TYPE_LABELS[b.type], b.value, "—",
                         f"차단 해제 · {REASON_SHORT[self.released[b.key]]}" if released else "차단",
                         b.reason, "muted" if released else "danger"))
        return rows

    def _fill_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._row_keys = {}
        for n, (key, typ, value, repl, state, why, tag) in enumerate(self._rows()):
            self._row_keys[str(n)] = key
            self.tree.insert("", "end", iid=str(n), tags=(tag,) if tag else (),
                             values=(typ, _short(value or ""), _short(repl), state, why))

    def _update_banner(self) -> None:
        """톤: 빨강=기밀·오류, 노랑=민감, 초록=공개, 중립=검사 중·빈 글. 버튼은 오른쪽."""
        g = self.grade()
        res = self.result
        lines = []
        for b in (self.btn_retry, self.btn_local, self.btn_internal, self.btn_settings):
            b.pack_forget()
        if g == "C":
            tone, title = "danger", "기밀 · 외부 AI 전송이 차단됐어요"
            lines += [f"• {b.reason}" for b in self._active_blockers()]
            lines.append("→ 해당 부분을 클릭해 '이 부분 빼고 다시 검사'를 고르면 외부 AI에 보낼 수 있어요. "
                         "그대로 써야 하면 사내 AI를 이용하세요.")
            self.btn_internal.pack(side="right")
            self.btn_send.configure(text="외부 전송 차단됨", state="disabled")
        elif self.loading:
            tone, title = "neutral", "서버에서 검사 중…"
            lines.append(f"{_host(self.app)}에 보내 검사하고 있어요.")
            self.btn_send.configure(text="변환문 복사", state="disabled")
        elif self.error is not None:
            tone, title = "danger", "서버에서 검사하지 못했어요"
            lines += [self.error.message, "다시 시도하거나 이 PC에서 검사하세요."]
            self.btn_local.pack(side="right")
            if self.error.kind in CERT_KINDS | AUTH_KINDS:
                self.btn_settings.pack(side="right", padx=(0, theme.px(8)))
            self.btn_retry.pack(side="right", padx=(0, theme.px(8)))
            self.btn_send.configure(text="변환문 복사", state="disabled")
        elif res is None:
            tone, title = "neutral", "검사할 프롬프트를 붙여넣거나 입력하세요"
            self.btn_send.configure(text="변환문 복사", state="disabled")
        else:
            if g == "O":
                tone, title = "success", "공개 · 민감정보가 보이지 않아요"
                self.btn_send.configure(text="그대로 복사", state="normal")
            else:
                tone = "danger" if g == "C2" else "warning"
                if g == "C2":
                    title = f"기밀 · 비밀값을 포함해 {res.detected_count}곳을 서버에서 가렸어요"
                    lines.append("• API 키·토큰·비밀번호가 있어요. 변환문에서는 가렸고, 답변을 복원해도 되돌리지 않아요.")
                elif res.detected_count:
                    title = f"민감 · 개인정보 {res.detected_count}곳을 서버에서 가렸어요"
                else:
                    title = f"민감 · {len({r.value for r in self.repls})}곳을 이 PC에서 가렸어요"
                self.btn_send.configure(text="변환문 복사", state="normal")
            org = {r.value for r in self.repls if r.kind == "org"}
            if org:
                lines.append(f"• 조직 베이스(코드네임·고객사·사내 도메인) {len(org)}건은 서버로 보내기 전에 이 PC에서 가렸어요.")
            if self.reverted or self.org_reverted:
                lines.append(f"• 되돌린 항목 {len(self.reverted) + len(self.org_reverted)}건은 원문 그대로 나가요.")
            unsure = len({it.token for it in self.items if not it.exact})
            if unsure:
                lines.append(f"• 원문 위치를 확정하지 못한 항목 {unsure}건은 되돌릴 수 없어요 (가린 채로 보내요).")
            if res.remaining_count:
                lines.append(f"• 가린 글을 서버가 다시 검사했더니 {res.remaining_count}곳이 더 의심돼요. 변환문을 한 번 더 확인하세요.")
            if res.rules_only or res.confidence != "high":
                lines.append("• 서버에 탐지 모델이 없어 규칙만으로 검사했어요. 이름·주소는 검사하지 않았어요.")
            if g != "O":
                lines.append("변환문을 복사해 붙여넣으세요. AI 답변은 검사 후 10분 안에 '답변 복원'으로 한 번 되돌릴 수 있어요.")
        if self.app.server and self.app.server.insecure:
            lines.append("• 서버 연결이 암호화되지 않았어요(HTTP). 실제 개인정보로 쓰기 전에 HTTPS로 바꿔야 해요.")
        self.banner.set(tone, title, "\n".join(lines))

    # ================================================================== 클릭 → 사용자 행동
    def _describe(self, key) -> str:
        kind, ref = key
        if kind == "t":
            it = next((i for i in self.items if i.token == ref), None)
            if it is None:
                return ref
            state = "되돌림" if ref in self.reverted else "적용"
            return f"{it.label} · {state}\n서버 탐지 → {ref}\n클릭해서 되돌리기"
        if kind == "o":
            return f"조직 베이스 · {'되돌림' if ref in self.org_reverted else '적용'}\n서버로 보내기 전에 이 PC에서 가림\n클릭해서 되돌리기"
        if kind == "b":
            return f"기밀 · {self.blockers[ref].reason}\n클릭해서 빼고 보내기·차단 해제"
        return "직접 신고한 값\n클릭해서 신고 취소"

    def _show_tip(self, event, key) -> None:
        self.tip.show(event.x_root, event.y_root, self._describe(key))

    def _on_tag_click(self, event, key, widget) -> None:
        if widget is self.orig and self.orig.tag_ranges("sel"):
            return
        first = self._pending_click is None
        self._pending_click = (key, event.x_root, event.y_root)
        if first:
            self.after_idle(self._open_pending)

    def _open_pending(self) -> None:
        if self._pending_click:
            key, x, y = self._pending_click
            self._pending_click = None
            self._open_menu(key, x, y)

    def _on_tree_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row and row in self._row_keys:
            self._open_menu(self._row_keys[row], event.x_root, event.y_root)

    def _reason_menu(self, parent: tk.Menu, apply) -> tk.Menu:
        sub = theme.menu(parent)
        for code, label in REASONS:
            sub.add_command(label=label, command=lambda c=code: apply(c))
        return sub

    def _open_menu(self, key, x: int, y: int) -> None:
        self.tip.hide()
        kind, ref = key
        m = theme.menu(self)
        if kind == "t":
            it = next((i for i in self.items if i.token == ref), None)
            if it is None:
                return
            m.add_command(label=f"{it.label} · {_short(theme.masked(it.value) if it.value else '(위치 불명)', 30)}  →  {ref}", state="disabled")
            m.add_separator()
            if not it.exact:
                m.add_command(label="원문 위치를 확정하지 못해 되돌릴 수 없어요", state="disabled")
            elif ref in self.reverted:
                m.add_command(label="다시 가리기", command=lambda: self._set(self.reverted, ref, None))
            else:
                m.add_cascade(label="되돌리기 · 원문 그대로 보내기 (사유 선택)",
                              menu=self._reason_menu(m, lambda c: self._set(self.reverted, ref, c)))
                m.add_command(label="이 값은 앞으로 탐지하지 않기 (오탐 등록)", command=lambda: self._ignore(it.value, ref))
        elif kind == "o":
            m.add_command(label=f"조직 베이스 · {_short(theme.masked(ref), 30)}", state="disabled")
            m.add_separator()
            if ref in self.org_reverted:
                m.add_command(label="다시 가리기", command=lambda: self._set(self.org_reverted, ref, None, recheck=True))
            else:
                m.add_cascade(label="되돌리기 · 원문 그대로 보내기 (사유 선택)",
                              menu=self._reason_menu(m, lambda c: self._set(self.org_reverted, ref, c, recheck=True)))
        elif kind == "b":
            b = self.blockers[ref]
            m.add_command(label=f"{TYPE_LABELS[b.type]} · {_short(b.value, 30)}", state="disabled")
            m.add_separator()
            if b.key in self.released:
                m.add_command(label="다시 차단", command=lambda: self._set(self.released, b.key, None))
            else:
                m.add_command(label="이 부분 빼고 다시 검사", command=lambda: self._cut(b))
                m.add_cascade(label="차단 해제 (사유 선택)",
                              menu=self._reason_menu(m, lambda c: self._set(self.released, b.key, c)))
        else:
            m.add_command(label="신고 취소 (다시 보내기)", command=lambda: self._unreport(ref))
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _set(self, store: dict, key, reason, recheck: bool = False) -> None:
        """recheck: 서버로 보낼 글이 바뀌는 변경(이 PC에서 미리 가린 값)이면 서버에 다시 묻는다."""
        if reason is None:
            store.pop(key, None)
        else:
            store[key] = reason
        self.check() if recheck else self.render()

    def _ignore(self, value: str, token: str) -> None:
        self.app.ignore.add(value)
        self._set(self.reverted, token, "false_positive")

    def _cut(self, blocker) -> None:
        """기밀 부분을 원문에서 지우고 서버에 다시 묻는다."""
        self.orig.delete(theme.index(blocker.start), theme.index(blocker.end))  # <<Modified>> → check()

    # ================================================================== 놓친 정보 신고
    def _on_right_click(self, event) -> None:
        m = theme.menu(self)
        try:
            selected = self.orig.get("sel.first", "sel.last").strip()
        except tk.TclError:
            selected = ""
        if selected:
            sub = theme.menu(m)
            for typ, label in MISSED_TYPES:
                sub.add_command(label=label, command=lambda t=typ, v=selected: self._report(t, v))
            m.add_cascade(label=f"이것도 가려줘 · 놓친 정보 신고  “{_short(theme.masked(selected), 16)}”", menu=sub)
            m.add_separator()
        m.add_command(label="붙여넣기", command=lambda: self.orig.event_generate("<<Paste>>"))
        m.add_command(label="복사", command=lambda: self.orig.event_generate("<<Copy>>"))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _report(self, typ: str, value: str) -> None:
        self.manual.append((typ, value))
        self.app.ignore.discard(value)
        self.app.log.record(service=f"server:{self.source}", grade="S", action="missed_report", detect_types=[typ],
                            counts={typ: 1}, reason=f"shape={shape_of(value)}", max_confidence=1.0)
        self.check()  # 신고한 값은 서버로 보내기 전에 가리므로 다시 묻는다

    def _unreport(self, idx: int) -> None:
        if 0 <= idx < len(self.manual):
            self.manual.pop(idx)
        self.check()

    # ================================================================== 전송 행동
    def _reasons(self) -> list[str]:
        return list(self.reverted.values()) + list(self.org_reverted.values()) + list(self.released.values())

    def _reason(self) -> str | None:
        reasons = self._reasons()
        return max(reasons, key=lambda r: REASON_PRIORITY.get(r, 0)) if reasons else None

    def _sent_tokens(self) -> frozenset:
        """보낼 글에 남은 서버 자리표시 (AI 답변을 복사했을 때 복원 창을 띄울 단서)."""
        return frozenset(mark[1] for _, mark in self.segs if mark and mark[0] == "token")

    def _log_local(self, action: str, reason: str | None = None) -> None:
        """이 PC의 통계용 기록 (원문 없이 유형·건수·사유만). 서버 기록은 /decide가 한다."""
        org = self._org_values()
        types = [LABEL_TYPE.get(it.label, "other") for it in self.items] + [s.type for s in org]
        types += [typ for typ, _ in self.manual] + [b.type for b in self.blockers]
        if not types:
            return
        reverted = ([LABEL_TYPE.get(it.label, "other") for it in self.items if it.token in self.reverted]
                    + [s.type for s in org if s.value in self.org_reverted])
        grade = {"C": "C", "C2": "C", "S": "S"}.get(self.grade(), "O")
        self.app.log.record(service=f"server:{self.source}", grade=grade, action=action, detect_types=types,
                            counts=dict(Counter(types)), reverted_types=reverted,
                            reasons=self._reasons() if reason is None else (), reason=reason,
                            max_confidence=0.9 if self.result and self.result.confidence == "high" else 0.5)

    def on_send(self) -> None:
        g = self.grade()
        if g is None or g == "C":
            return
        if not self._fresh():  # 고친 뒤 아직 서버 결과가 안 옴
            self.check()
            self.app.toast("고친 내용을 서버에서 다시 검사하고 있어요. 끝나면 다시 눌러 주세요.")
            return
        self.app.copy_to_clipboard(self.outgoing)
        action = "send_as_is" if g == "O" else "send_transformed"
        self.app.server_decide(self.result, action, self._reason(), retry_text=self.result_sent,
                               tokens=self._sent_tokens())
        self._log_local("sent_sanitized")
        self.app.toast("변환문을 복사했어요. Claude 입력창에 붙여넣으세요 (Ctrl+V)." if g != "O"
                       else "복사했어요. 붙여넣어 사용하세요.")
        self._close()

    def on_original(self) -> None:
        dlg = ReasonDialog(self, blocked=self.grade() == "C",
                           note="원문은 저장되지 않고, 유형·건수·사유와 원문 해시가 서버 로그에 남아요.")
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self.app.copy_to_clipboard(self._text())
        if self._fresh():
            action = "send_as_is" if self.grade() == "O" else "revert_original"
            self.app.server_decide(self.result, action, dlg.result)
        self._log_local("sent_original", reason=dlg.result)
        self.app.toast("원문을 복사했어요 · 선택한 사유가 기록됐어요")
        self._close()

    def on_cancel(self) -> None:
        if self._fresh():
            self.app.server_decide(self.result, "cancel")
        self._log_local("cancelled")
        self._close()

    def on_internal(self) -> None:
        self.app.copy_to_clipboard(self._text())
        if self._fresh():
            self.app.server_decide(self.result, "cancel")  # 외부 AI로 보내지 않음
        self._log_local("route_internal")
        self.app.toast("[데모] 사내 AI로 안내합니다. 원문을 복사했으니 사내 AI 입력창에 붙여넣으세요.")
        self._close()

    def on_local(self) -> None:
        """서버에 닿지 않을 때: 같은 글을 이 PC의 엔진으로 검사한다."""
        text = self._text()
        self._close()
        self.app.open_review(text, self.source, local=True)

    def _load_clipboard(self) -> None:
        text = platform_api.get_clipboard_text()
        if text:
            self.orig.delete("1.0", "end")
            self.orig.insert("1.0", text)

    def refresh(self) -> None:
        self.render()

    def _close(self) -> None:
        self._gen += 1  # 늦게 온 서버 결과는 버린다
        self.tip.hide()
        self.app.review_closed(self)
        self.destroy()


class ServerRestoreWindow(tk.Toplevel):
    """AI 답변 속 서버 자리표시([이름_1] 등)를 서버에서 원래 값으로 되돌린다. 서버 작업당 한 번만 된다.

    주요 버튼(앰버)은 하나이고 지금 할 일을 따른다: 복원 전에는 '서버에서 복원', 복원한 뒤에는 '복원 결과 복사'.
    """

    def __init__(self, app, text: str = ""):
        super().__init__(app.root)
        self.app = app
        self.result = ""
        self._q: queue.Queue = queue.Queue()
        theme.window(self, "답변 복원 (서버) — KP Guard", 1080, 680, 820, 520)
        self.protocol("WM_DELETE_WINDOW", self._close)
        build_restore_layout(self, "AI 답변 (자리표시 포함)",
                             "서버는 검사 후 10분 안에 한 번만 복원해요. 비밀값(API 키·비밀번호)은 되돌리지 않아요")
        self.btn_run = theme.Button(self.bottom, "서버에서 복원", self.run, kind="primary")
        self.btn_run.pack(side="right")
        theme.Button(self.bottom, "닫기", self._close, kind="ghost").pack(side="right", padx=theme.px(8))
        if text:
            self.src.insert("1.0", text)
        self._update_info()
        self.lift()
        self.focus_force()
        self.after(100, self._poll)

    def _update_info(self, note: str = "", restored: bool = False, failed: bool = False, warn: bool = False) -> None:
        work = self.app.server_work
        if failed:
            tone, title, msg = "danger", "서버에서 복원하지 못했어요", ""
        elif restored:
            tone = "warning" if warn else "success"
            title = "서버에서 복원했어요 · 확인이 필요해요" if warn else "서버에서 원래 값으로 복원했어요"
            msg = "이 작업은 서버에서 지워져 다시 복원할 수 없어요"
        elif work and work.alive():
            left = int(work.expires_in() // 60)
            tone, title = "neutral", "AI 답변 복원 (서버)"
            msg = f"마지막으로 보낸 변환문의 매핑으로 복원해요 · 서버 보관 약 {left}분 남음 · 한 번 복원하면 서버에서 지워져요"
        else:
            tone, title = "neutral", "서버에 복원할 작업이 없어요"
            msg = "변환문을 보낸 뒤 10분 안에만 복원할 수 있어요. 조직 베이스 태그는 이 PC에서 복원해요."
        self.banner.set(tone, title, "\n".join(x for x in (msg, note) if x))

    def run(self) -> None:
        reply = self.src.get("1.0", "end-1c")
        if not reply.strip():
            return
        work = self.app.server_work
        if not (work and work.alive()) or self.app.server is None:
            self._show(reply, reply, self._file_note(set(), {m.group() for m in PLACEHOLDER.finditer(reply)}))
            return
        self.app.server_work = None  # 서버는 한 번 복원하면 작업을 지운다
        self.btn_run.configure(state="disabled", text="복원 중…")
        server = self.app.server

        def job():
            try:
                res, err = server.restore(work.work_id, reply), None
            except ServerError as exc:
                res, err = None, exc
            except Exception:
                res, err = None, ServerError("서버 요청 중 오류가 났어요.")
            self._q.put((reply, work, res, err))

        threading.Thread(target=job, daemon=True, name="server-restore").start()

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            reply, work, res, err = self._q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        self.btn_run.configure(state="normal", text="서버에서 복원")
        if err is not None:
            if (err.kind in NOT_PROCESSED or err.kind == "failed") and work.alive() and self.app.server_work is None:
                self.app.server_work = work  # 서버가 복원하지 않았으면 작업이 남아 있으니 다시 시도할 수 있다
            self._show(reply, reply, err.message, failed=True)
        else:
            note = f"그대로 둔 비밀값 {len(res.blocked)}개" if res.blocked else ""
            if res.unknown:
                note += (" · " if note else "") + f"이번 작업에 없는 자리표시 {len(res.unknown)}개는 그대로 뒀어요"
            tokens = {m.group() for m in PLACEHOLDER.finditer(reply)}
            swapped = tokens - set(res.unknown) - set(res.blocked)
            files = self._file_note(swapped, tokens & set(res.unknown))
            self._show(reply, res.text, "\n".join(x for x in (note, files) if x), restored=True,
                       warn=bool(swapped & self.app.recent_file_tokens()))
        self.after(100, self._poll)

    def _file_note(self, swapped: set, left: set) -> str:
        """서버 파일 보호본에도 같은 자리표시가 쓰였을 수 있다 (프롬프트와 파일은 서버에서 번호를 따로 매긴다)."""
        files = self.app.recent_file_tokens()
        clash, kept = sorted(swapped & files), sorted(left & files)
        lines = []
        if clash:
            more = " 등" if len(clash) > 3 else ""
            lines.append(f"• {', '.join(clash[:3])}{more}은(는) 최근 서버에서 검사한 파일 보호본에도 있던 자리표시예요. "
                         "프롬프트 값으로 바꿨으니, 파일 내용을 가리킨 거라면 틀린 값이에요. 확인하세요.")
        if kept:
            lines.append(f"• 파일 보호본의 자리표시 {len(kept)}개는 아직 복원할 수 없어 그대로 뒀어요 (서버에 파일 복원 기능이 없어요).")
        return "\n".join(lines)

    def _show(self, reply: str, server_text: str, note: str, restored: bool = False, failed: bool = False,
              warn: bool = False) -> None:
        """서버 결과에 이 PC의 매핑(조직 베이스 태그)까지 되돌리고, 서버가 바꾼 곳에 밑줄을 긋는다."""
        text, local_ranges = local_restore(server_text, self.app.session.entries)
        ranges = local_ranges if server_text == reply else [
            (it.start, it.end) for it in align(text, reply) if it.exact] or local_ranges
        self.result = text
        self.dst.configure(state="normal")
        self.dst.delete("1.0", "end")
        self.dst.insert("1.0", text)
        for s, e in ranges:
            self.dst.tag_add("restored", theme.index(s), theme.index(e))
        self.dst.configure(state="disabled")
        self._update_info(note, restored, failed, warn)
        if not failed:  # 지금 할 일이 '복사'로 바뀐다
            self.btn_run.configure(text="복원 결과 복사", command=self._copy)

    def _paste(self) -> None:
        text = platform_api.get_clipboard_text()
        if text:
            self.src.delete("1.0", "end")
            self.src.insert("1.0", text)

    def _copy(self) -> None:
        if self.result:
            self.app.copy_to_clipboard(self.result)
            self.app.toast("복원 결과를 복사했어요")

    def _close(self) -> None:
        self.app.restore_closed(self)
        self.destroy()
