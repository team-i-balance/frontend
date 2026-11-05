"""점검 창 (§7 변환 미리보기의 데스크톱판, 이 PC에서 처리).

흐름: 원문 ↔ 변환문 → 하이라이트/표 클릭 → 사용자 행동(되돌리기+사유 / 다시 가리기 / 방식 변경 / 오탐 등록)
     → [변환문 복사] / [취소] / [원문 그대로 사용(사유)] → 큐 분류 로그

화면 규칙(디자인 설정): 상태는 배너 톤(초록=공개, 노랑=민감, 빨강=기밀)과 문구로 함께 알린다. 앰버는 주요 버튼
'변환문 복사' 하나에만. 원문 쪽 하이라이트는 가린 원문 표시(빨강), 바뀐 값은 초록, 확인 필요는 노랑.
표·메뉴·툴팁의 원문 값은 일부만 보여 준다.
"""
from __future__ import annotations

import tkinter as tk
from collections import Counter
from tkinter import ttk

from ..detect.pipeline import COVERAGE_LABELS, detect
from ..detect.scoring import overall_grade
from ..detect.span import TYPE_LABELS
from ..sanitize.engine import ALLOWED, STRATEGY_LABELS, make_items, sanitize
from ..system import platform_api
from . import theme

REASONS = [("false_positive", "오탐이에요 (개인정보가 아님)"), ("business_need", "업무상 꼭 필요해요"),
           ("approved", "승인받았어요"), ("none", "선택 안 함")]
REASON_SHORT = {"false_positive": "오탐", "business_need": "업무상 필요", "approved": "승인받음", "none": "사유 없음"}
MISSED_TYPES = [("name", "이름"), ("phone", "전화번호"), ("address", "주소"), ("other", "기타")]
HINT = "놓친 정보는 원문에서 드래그한 뒤 우클릭해 가릴 수 있어요"
TABLE_CAPTION = "하이라이트나 행을 클릭하면 되돌리거나 변환 방식을 바꿀 수 있어요"

_short = theme.short
Tooltip = theme.Tooltip


def shape_of(value: str) -> str:
    """미탐 신고용: 값 대신 형태만 남긴다 (예: '숫자13 · 총 14자')."""
    digits = sum(c.isdigit() for c in value)
    hangul = sum("가" <= c <= "힣" for c in value)
    latin = sum(c.isascii() and c.isalpha() for c in value)
    parts = [f"{k}{n}" for k, n in (("숫자", digits), ("한글", hangul), ("영문", latin)) if n]
    return " ".join(parts) + f" · 총 {len(value)}자"


LOCAL_COLUMNS = (("type", "유형", 120), ("orig", "원문", 180), ("repl", "보낼 값", 170),
                 ("strategy", "방식", 70), ("state", "상태", 150), ("why", "근거", 330))


def build_layout(win, out_title: str, cols=LOCAL_COLUMNS):
    """점검 창 공통 배치: 배너 / [원문 카드 | 변환문 카드] / 탐지 항목 카드 / 하단(안내 왼쪽, 버튼 오른쪽).

    win에 banner, b_title, b_detail, b_actions, orig, out, out_info, cov_info, tree, bottom, hint 를 만든다.
    """
    gap = theme.px(16)
    win.banner = theme.Banner(win)
    win.banner.pack(fill="x", padx=gap, pady=(gap, 0))
    win.b_title, win.b_detail, win.b_actions = win.banner.title, win.banner.detail, win.banner.actions

    win.bottom = tk.Frame(win, bg=theme.BG_CANVAS)
    win.bottom.pack(side="bottom", fill="x", padx=gap, pady=(theme.px(8), gap))
    win.hint = theme.label(win.bottom, HINT)
    win.hint.pack(side="left")

    table = theme.Card(win, title="탐지 항목", caption=TABLE_CAPTION, fit=True)
    table.pack(side="bottom", fill="x", padx=gap, pady=(theme.px(12), 0))
    win.tree = theme.table(table.body, cols, height=3 if theme.compact(win) else 4)

    body = ttk.PanedWindow(win, orient="horizontal")
    body.pack(fill="both", expand=True, padx=gap, pady=(theme.px(12), 0))
    left = theme.Card(body, title="원문", caption="직접 입력하거나 고칠 수 있어요")
    right = theme.Card(body, title=out_title)
    body.add(left, weight=1)
    body.add(right, weight=1)
    theme.Button(left.actions, "모두 지우기", lambda: win.orig.delete("1.0", "end"), kind="ghost",
                 size="sm").pack(side="right")
    theme.Button(left.actions, "클립보드에서 불러오기", win._load_clipboard, kind="ghost", size="sm").pack(side="right")
    win.orig = theme.scrolled_text(left.body, undo=True)
    win.cov_info = theme.Badge(right.actions)
    win.cov_info.pack(side="right")
    win.out_info = theme.label(right.body, "")
    win.out_info.pack(fill="x", pady=(0, theme.px(8)))
    win.out = theme.scrolled_text(right.body, state="disabled", cursor="arrow")


class ReviewWindow(tk.Toplevel):
    def __init__(self, app, text: str = "", source: str = "manual"):
        super().__init__(app.root)
        self.app = app
        self.source = source
        self.decisions: dict[tuple, dict] = {}
        self.manual: list[tuple[str, str]] = []
        self.ignored_types: list[str] = []
        self.items = []
        self.notes: list[str] = []
        self.coverage = "rules"
        self.result = None
        self.grade = "O"
        self._refresh_job = None
        self._pending_click = None
        self._menu_var = tk.StringVar(self)
        self.tip = Tooltip(self)

        theme.window(self, "AI 전송 전 점검 — KP Guard", 1200, 840, 900, 640)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self._build()
        if text:
            self.orig.insert("1.0", text)
        self.orig.edit_modified(False)
        self.refresh()
        self._bring_front()

    # ================================================================== UI 구성
    def _build(self) -> None:
        self.btn_send = None
        build_layout(self, "AI에 보낼 변환문")
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

    # ================================================================== 탐지·변환
    def _on_modified(self, _event=None) -> None:
        if not self.orig.edit_modified():
            return
        self.orig.edit_modified(False)
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(350, self.refresh)

    def refresh(self) -> None:
        self._refresh_job = None
        text = self.orig.get("1.0", "end-1c")
        det = detect(text, self.app.base, self.app.ignore, self.manual, model=self.app.detector())
        self.notes = det.notes
        self.coverage = det.coverage
        self.items = make_items(det, self.decisions)
        self.result = sanitize(text, self.items, self.app.session)
        self.grade = overall_grade(self.items)
        self._paint_original()
        self._paint_output()
        self._fill_table()
        self._update_banner()

    # ================================================================== 그리기
    @staticmethod
    def _style_orig(it) -> dict:
        """원문 쪽: 가릴 값은 가린 원문 표시(빨강), 되돌린 값은 회색 밑줄, 확인 필요는 노랑 밑줄."""
        if it.is_blocker:
            if not it.applied:
                return dict(theme.KEPT)
            return {**theme.BLOCKED, "font": theme.F["mark16"]}
        if it.applied:
            return {**theme.MARK, "font": theme.F["mark16"]}
        if it.revert_reason:
            return dict(theme.KEPT)
        return dict(theme.SUSPECT)

    @staticmethod
    def _style_out(it) -> dict:
        """변환문 쪽: 바꾼 값은 초록, 원문 그대로 나가는 값은 빨강 밑줄, 확인 필요는 노랑 밑줄."""
        if it.applied and not it.is_blocker:
            return dict(theme.SAFE)
        if it.revert_reason or (it.is_blocker and not it.applied):
            return dict(theme.RAW)
        return dict(theme.SUSPECT)

    def _bind_tag(self, widget: tk.Text, tag: str, idx: int) -> None:
        widget.tag_bind(tag, "<ButtonRelease-1>", lambda e, i=idx, w=widget: self._on_tag_click(e, i, w))
        widget.tag_bind(tag, "<Enter>", lambda e, i=idx: self._show_tip(e, i))
        widget.tag_bind(tag, "<Leave>", lambda e: self.tip.hide())

    def _clear_tags(self, widget: tk.Text) -> None:
        for tag in widget.tag_names():
            if tag.startswith("it"):
                widget.tag_delete(tag)

    def _paint_original(self) -> None:
        self._clear_tags(self.orig)
        for i, it in enumerate(self.items):
            tag = f"it{i}"
            self.orig.tag_add(tag, theme.index(it.span.start), theme.index(it.span.end))
            self.orig.tag_configure(tag, **self._style_orig(it))
            self._bind_tag(self.orig, tag, i)
            if it.is_blocker:
                self.orig.tag_lower(tag)  # 블록 안의 개별 항목이 위에 보이고 먼저 클릭되도록
        self.orig.tag_raise("sel")

    def _paint_output(self) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self._clear_tags(self.out)
        self.out.insert("1.0", self.result.text)
        for i, it in enumerate(self.items):
            if it.out_start < 0:
                continue
            tag = f"it{i}"
            self.out.tag_add(tag, theme.index(it.out_start), theme.index(it.out_end))
            self.out.tag_configure(tag, **self._style_out(it))
            self._bind_tag(self.out, tag, i)
        self.out.configure(state="disabled")
        applied = sum(1 for it in self.items if it.applied and not it.is_blocker)
        deleted = sum(1 for it in self.items if it.applied and it.strategy == "delete")
        self.out_info.configure(text=f"바꾼 값 {applied}개 · 삭제한 {deleted}개는 변환문에 보이지 않아요"
                                if applied else "")
        self.cov_info.set(*self._coverage_label())

    def _coverage_label(self) -> tuple[str, str]:
        """어느 단계까지 검사했는지 (모델이 꺼져 있거나 실패하면 그대로 알린다)."""
        if self.coverage == "model_failed":
            return "규칙·사전만 · 소형 모델 실패", "warning"
        if self.coverage == "model_skipped":
            return "규칙·사전만 · 글이 길어 모델 생략", "warning"
        if self.coverage == "rules" and self.app.models.state == "loading":
            return "규칙·사전 · 모델 준비 중", "neutral"
        return f"검사: {COVERAGE_LABELS[self.coverage]}", "neutral"

    def _state_text(self, it) -> tuple[str, str]:
        if it.is_blocker:
            if not it.applied:
                return f"차단 해제 · {REASON_SHORT.get(it.revert_reason, '')}", "muted"
            return ("빼고 보냄" if it.strategy == "delete" else "차단"), "danger"
        if it.applied:
            return "적용", ""
        if it.revert_reason:
            return f"되돌림 · {REASON_SHORT[it.revert_reason]}", "muted"
        return "확인 필요", "warning"

    def _fill_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, it in enumerate(self.items):
            s = it.span
            if it.is_blocker:
                repl = "(삭제)" if it.strategy == "delete" and it.applied else "—"
            elif not it.applied:
                repl = "(원문 유지)"
            elif it.strategy == "delete":
                repl = "(삭제)"
            else:
                repl = it.replacement
            state, tag = self._state_text(it)
            self.tree.insert("", "end", iid=str(i), tags=(tag,) if tag else (), values=(
                TYPE_LABELS[s.type], _short(theme.masked(s.value)), _short(repl), STRATEGY_LABELS[it.strategy], state,
                f"{s.reason} · 신뢰도 {s.confidence:.2f}"))

    def _update_banner(self) -> None:
        self.btn_internal.pack_forget()
        normal = [it for it in self.items if not it.is_blocker]
        applied = sum(1 for it in normal if it.applied)
        suspects = sum(1 for it in normal if it.suspect)
        reverted = sum(1 for it in normal if it.revert_reason)
        lines = []
        if self.grade == "C":
            title = "기밀 · 외부 AI 전송이 차단됐어요"
            for it in self.items:
                if it.is_blocker and it.applied and it.strategy == "block":
                    lines.append(f"• {it.span.reason}")
            lines.append("→ 해당 부분을 클릭해 '이 부분 빼고 보내기'를 고르면 외부 AI에 보낼 수 있어요. "
                         "그대로 써야 하면 사내 AI를 이용하세요.")
            lines += [f"• {n}" for n in self.notes]
            self.btn_internal.pack(side="right")
            self.btn_send.configure(text="외부 전송 차단됨", state="disabled")
        elif self.grade == "S":
            title = f"민감 · 개인정보 {applied}건을 안전한 값으로 바꿨어요"
            if suspects:
                lines.append(f"• 확인이 필요한 항목 {suspects}건(노란 밑줄)은 아직 바꾸지 않았어요. 클릭해서 가릴 수 있어요.")
            if reverted:
                lines.append(f"• 되돌린 항목 {reverted}건은 원문 그대로 나가요.")
            lines += [f"• {n}" for n in self.notes]
            lines.append("변환문을 복사해 Claude Code·Codex·채팅창에 붙여넣으세요. AI 답변은 '답변 복원'으로 원래 값으로 바꿀 수 있어요.")
            self.btn_send.configure(text="변환문 복사", state="normal")
        else:
            title = "공개 · 민감정보가 보이지 않아요"
            lines += [f"• {n}" for n in self.notes]
            lines.append("그대로 보내도 돼요. 놓친 정보가 있으면 드래그 후 우클릭으로 가릴 수 있어요.")
            self.btn_send.configure(text="그대로 복사", state="normal")
        self.banner.set(theme.GRADE_TONE[self.grade], title, "\n".join(lines))

    # ================================================================== 클릭 → 사용자 행동
    def _show_tip(self, event, idx: int) -> None:
        if idx >= len(self.items):
            return
        it = self.items[idx]
        s = it.span
        state, _ = self._state_text(it)
        text = (f"{TYPE_LABELS[s.type]} · {state}\n근거: {s.reason}\n신뢰도 {s.confidence:.2f} · "
                f"방식: {STRATEGY_LABELS[it.strategy]}\n클릭해서 되돌리기·방식 변경")
        self.tip.show(event.x_root, event.y_root, text)

    def _on_tag_click(self, event, idx: int, widget: tk.Text) -> None:
        if widget is self.orig and self.orig.tag_ranges("sel"):
            return  # 드래그 선택 중이면 메뉴 대신 선택 유지 (놓친 정보 신고용)
        # 겹친 태그는 낮은 우선순위부터 모두 불린다 → 마지막(가장 위) 것만 연다
        first = self._pending_click is None
        self._pending_click = (idx, event.x_root, event.y_root)
        if first:
            self.after_idle(self._open_pending_menu)

    def _open_pending_menu(self) -> None:
        if self._pending_click:
            idx, x, y = self._pending_click
            self._pending_click = None
            self._open_menu(idx, x, y)

    def _on_tree_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self._open_menu(int(row), event.x_root, event.y_root)

    def _reason_menu(self, parent: tk.Menu, it) -> tk.Menu:
        sub = theme.menu(parent)
        for code, text in REASONS:
            sub.add_command(label=text, command=lambda c=code: self._decide(it, applied=False, reason=c))
        return sub

    def _open_menu(self, idx: int, x: int, y: int) -> None:
        self.tip.hide()
        if idx >= len(self.items):
            return
        it = self.items[idx]
        s = it.span
        m = theme.menu(self)
        head = f"{TYPE_LABELS[s.type]} · {_short(theme.masked(s.value), 30)}"
        if it.applied and it.replacement:
            head += f"  →  {it.replacement}"
        m.add_command(label=head, state="disabled")
        m.add_command(label=f"근거: {s.reason} (신뢰도 {s.confidence:.2f})", state="disabled")
        m.add_separator()

        if it.is_blocker:
            if it.applied and it.strategy == "block":
                m.add_command(label="이 부분 빼고 보내기", command=lambda: self._decide(it, applied=True, strategy="delete"))
                m.add_cascade(label="차단 해제하고 원문 유지 (사유 선택)", menu=self._reason_menu(m, it))
            else:
                m.add_command(label="다시 차단", command=lambda: self._decide(it, applied=True, strategy="block"))
        else:
            if it.applied:
                m.add_cascade(label="되돌리기 · 원문 그대로 보내기 (사유 선택)", menu=self._reason_menu(m, it))
            else:
                m.add_command(label="가리기 (변환 적용)", command=lambda: self._decide(it, applied=True))
            strategies = theme.menu(m)
            self._menu_var.set(it.strategy)
            for st in ALLOWED[s.type]:
                strategies.add_radiobutton(label=STRATEGY_LABELS[st], value=st, variable=self._menu_var,
                                           command=lambda st=st: self._decide(it, applied=True, strategy=st))
            m.add_cascade(label="변환 방식 바꾸기", menu=strategies)
            m.add_separator()
            m.add_command(label="이 값은 앞으로 탐지하지 않기 (오탐 등록)", command=lambda: self._ignore(it))
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _decide(self, it, **change) -> None:
        d = self.decisions.setdefault(it.span.key, {})
        if change.get("applied"):
            change.setdefault("reason", None)
        d.update(change)
        self.refresh()

    def _ignore(self, it) -> None:
        self.app.ignore.add(it.span.value)
        self.ignored_types.append(it.span.type)
        self.refresh()

    # ================================================================== 놓친 정보 신고
    def _on_right_click(self, event) -> None:
        m = theme.menu(self)
        try:
            selected = self.orig.get("sel.first", "sel.last")
        except tk.TclError:
            selected = ""
        if selected.strip():
            sub = theme.menu(m)
            for typ, text in MISSED_TYPES:
                sub.add_command(label=text, command=lambda t=typ, v=selected.strip(): self._report_missed(t, v))
            m.add_cascade(label=f"이것도 가려줘 · 놓친 정보 신고  “{_short(theme.masked(selected.strip()), 16)}”", menu=sub)
            m.add_separator()
        m.add_command(label="붙여넣기", command=lambda: self.orig.event_generate("<<Paste>>"))
        m.add_command(label="복사", command=lambda: self.orig.event_generate("<<Copy>>"))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _report_missed(self, typ: str, value: str) -> None:
        self.manual.append((typ, value))
        self.app.ignore.discard(value)
        # 값은 기록하지 않는다: 유형과 형태만 (§11)
        self.app.log.record(service=self.source, grade="S", action="missed_report", detect_types=[typ],
                            counts={typ: 1}, reason=f"shape={shape_of(value)}", max_confidence=1.0)
        self.refresh()

    # ================================================================== 전송 행동
    def _summary(self):
        types = [it.span.type for it in self.items]
        reverted = [it for it in self.items if not it.applied and it.revert_reason]
        reverted_types = [it.span.type for it in reverted] + self.ignored_types
        reasons = [it.revert_reason for it in reverted] + ["false_positive"] * len(self.ignored_types)
        max_conf = max((it.span.confidence for it in self.items), default=0.0)
        return types, reverted_types, reasons, max_conf

    def _log(self, action: str, reason: str | None = None) -> str | None:
        types, reverted_types, reasons, max_conf = self._summary()
        if not types and not reverted_types:
            return None  # 탐지가 없으면 남길 것도 없다
        return self.app.log.record(service=self.source, grade=self.grade, action=action, detect_types=types,
                                   counts=dict(Counter(types)), reverted_types=reverted_types,
                                   reasons=reasons if reason is None else (), reason=reason,
                                   max_confidence=max_conf)

    def on_send(self) -> None:
        if self.grade == "C":
            return
        self.app.copy_to_clipboard(self.result.text)
        self._log("sent_sanitized")
        msg = ("변환문을 복사했어요. Claude Code·Codex·채팅창에 붙여넣으세요 (Ctrl+V)"
               if self.grade == "S" else "복사했어요. 붙여넣어 사용하세요")
        self.app.toast(msg)
        self._close()

    def on_cancel(self) -> None:
        self._log("cancelled")
        self._close()

    def on_original(self) -> None:
        dlg = ReasonDialog(self, blocked=self.grade == "C")
        self.wait_window(dlg)
        if dlg.result is None:
            return
        self.app.copy_to_clipboard(self.orig.get("1.0", "end-1c"))
        queue = self._log("sent_original", reason=dlg.result)
        note = " · 보안 검토 대상으로 기록됐어요" if queue in ("violation", "candidate") else ""
        self.app.toast("원문을 복사했어요" + note)
        self._close()

    def on_internal(self) -> None:
        """조직 모드에서는 사내 AI 페이지를 새 탭으로 열고 확장이 입력창을 채운다 (§4). 지금은 흐름만."""
        self.app.copy_to_clipboard(self.orig.get("1.0", "end-1c"))
        self._log("route_internal")
        self.app.toast("[데모] 사내 AI로 안내해요. 원문을 복사했으니 사내 AI 입력창에 붙여넣으세요")
        self._close()

    def _load_clipboard(self) -> None:
        text = platform_api.get_clipboard_text()
        if text:
            self.orig.delete("1.0", "end")
            self.orig.insert("1.0", text)

    def _close(self) -> None:
        self.tip.hide()
        self.app.review_closed(self)
        self.destroy()


class ReasonDialog(theme.Dialog):
    """원문 전송 시 사유 선택. 사유는 큐 분류에만 쓰인다."""

    def __init__(self, master, blocked: bool, note: str = "원문은 기록되지 않고, 유형·건수·사유만 이 PC에 남아요."):
        super().__init__(master, "원문 그대로 사용")
        self.result = None
        msg = ("기밀로 판정된 내용이에요. 외부 AI에 보내면 보안 검토 대상으로 기록돼요."
               if blocked else "바꾸지 않은 원문을 복사해요. 이유를 알려주세요.")
        tk.Label(self.body, text=msg, font=theme.F["body16"], bg=theme.BG_RAISED,
                 fg=theme.TEXT_DANGER if blocked else theme.TEXT_PRIMARY, wraplength=theme.px(472),
                 justify="left", anchor="w").pack(fill="x")
        tk.Label(self.body, text=note, font=theme.F["label14r"], bg=theme.BG_RAISED, fg=theme.TEXT_SECONDARY,
                 wraplength=theme.px(472), justify="left", anchor="w").pack(fill="x", pady=(theme.px(4), theme.px(12)))
        self.var = tk.StringVar(value="none")
        for code, text in REASONS:
            ttk.Radiobutton(self.body, text=text, value=code, variable=self.var,
                            style="Dialog.TRadiobutton").pack(fill="x")
        theme.Button(self.buttons, "원문 복사", self._ok, kind="primary").pack(side="right")
        theme.Button(self.buttons, "돌아가기", self.destroy).pack(side="right", padx=theme.px(8))
        self.update_idletasks()
        self.geometry(f"+{master.winfo_rootx() + theme.px(300)}+{master.winfo_rooty() + theme.px(160)}")
        self.grab_set()

    def _ok(self) -> None:
        self.result = self.var.get()
        self.destroy()
