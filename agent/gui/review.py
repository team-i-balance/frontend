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
from ..sanitize.engine import ALLOWED, STRATEGY_LABELS, Item, make_items, replacement_for, sanitize
from ..sanitize.josa import ro
from ..system import platform_api
from . import theme
from .chip import Chip, ChipPanel

# 원문유지 사유 칩 셋 (입력창 칩과 같다)
REASONS = [("business_need", "업무상 필요"), ("false_positive", "오탐"), ("approved", "승인된 개인정보")]
REASON_SHORT = {"false_positive": "오탐", "business_need": "업무상 필요", "approved": "승인된 개인정보",
                "none": "사유 없음"}
MISSED_TYPES = [("name", "이름"), ("phone", "전화번호"), ("address", "주소"), ("other", "기타")]
HINT = "놓친 정보는 원문에서 드래그한 뒤 우클릭해 가릴 수 있어요"
TABLE_CAPTION = "하이라이트나 행을 누르면 마스킹 · 원문유지"

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
    gap = theme.px(24)
    win.banner = theme.Banner(win)
    win.banner.pack(fill="x", padx=gap, pady=(gap, 0))
    win.b_title, win.b_detail, win.b_actions = win.banner.title, win.banner.detail, win.banner.actions

    win.bottom = tk.Frame(win, bg=theme.BG_CANVAS)
    win.bottom.pack(side="bottom", fill="x", padx=gap, pady=(theme.px(8), gap))
    win.hint = theme.label(win.bottom, HINT)
    win.hint.pack(side="left")

    table = theme.Card(win, title="탐지 항목", caption=TABLE_CAPTION, fit=True)
    table.pack(side="bottom", fill="x", padx=gap, pady=(theme.px(16), 0))
    win.tree = theme.table(table.body, cols, height=3 if theme.compact(win) else 4)

    body = ttk.PanedWindow(win, orient="horizontal")
    body.pack(fill="both", expand=True, padx=gap, pady=(theme.px(16), 0))
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
    def __init__(self, app, text: str = "", source: str = "manual", selected=None):
        super().__init__(app.root)
        self.app = app
        self.source = source
        self.overlay_selection = selected
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
        self.chip = None
        self.tip = Tooltip(self)

        theme.window(self, f"AI 전송 전 점검 — {theme.APP_TITLE}", 1200, 840, 900, 640)
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
        self.btn_original = theme.Button(self.bottom, "원문 그대로 사용", self.on_original, kind="ghost")
        self.btn_original.pack(side="right")
        self.bind("<Control-Return>", lambda e: self.on_send())
        self.bind("<Escape>", lambda e: self._close_chip() if self.chip else self.on_cancel())
        self.bind("<ButtonPress>", self._close_chip, add="+")  # 칩 밖을 누르면 칩만 닫는다

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
            if self.overlay_selection == (it.span.start, it.span.end):
                self.overlay_selection = None
                self.after_idle(lambda idx=i, start=it.span.start: theme.open_at_mark(self, start, idx))
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
                return f"원문유지 · {REASON_SHORT.get(it.revert_reason, '')}", "muted"
            return ("빼고 보냄" if it.strategy == "delete" else "차단"), "danger"
        if it.applied:
            return "마스킹", ""
        if it.revert_reason:
            return f"원문유지 · {REASON_SHORT[it.revert_reason]}", "muted"
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
                lines.append(f"• 원문유지한 항목 {reverted}건은 원문 그대로 나가요.")
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

    # ------------------------------------------------------------------ 칩: 마스킹 / 원문유지
    def _close_chip(self, _e=None) -> None:
        if self.chip is not None:
            self.chip.destroy()
            self.chip = None

    def _chip_at(self, x: int, y: int) -> ChipPanel:
        self._close_chip()
        bounds = (0, 0, self.winfo_screenwidth(), self.winfo_screenheight())
        self.chip = ChipPanel(self, x, y + theme.px(4), bounds, below=y - theme.px(20))
        return self.chip

    def _item(self, key):
        return next((it for it in self.items if it.span.key == key), None)

    def _open_menu(self, idx: int, x: int, y: int) -> None:
        """하이라이트·행을 누르면 칩 두 개: 마스킹 / 원문유지."""
        self.tip.hide()
        if idx >= len(self.items):
            return
        self._chip_at(x, y)
        self._chip_root(self.items[idx].span.key)

    def _chip_root(self, key) -> None:
        it = self._item(key)
        if it is None or self.chip is None:
            return self._close_chip()
        state, _ = self._state_text(it)
        self.chip.show(title=f"{TYPE_LABELS[it.span.type]} · {state}", chips=[
            Chip("마스킹", lambda: self._chip_mask(key)),
            Chip("원문유지", lambda: self._chip_reasons(key)),
        ])

    def _chip_mask(self, key, strategy: str | None = None) -> None:
        """마스킹: 바뀔 값만 위에 보여 주고, 아래 칩으로 적용하거나 방식을 바꿔 본다."""
        it = self._item(key)
        if it is None or self.chip is None:
            return self._close_chip()
        if it.is_blocker:
            strategy, options = "delete", []
        else:
            strategy = strategy or it.strategy
            options = [st for st in ALLOWED[it.span.type] if st != strategy]
        done = it.applied and it.strategy == strategy
        if strategy in ("delete", "block"):
            word, deleted = theme.masked(it.span.value), True
        else:
            text = self.orig.get("1.0", "end-1c")
            word, deleted = replacement_for(Item(it.span, strategy, True), self.app.session, text), False
        chips = [Chip("적용됨" if done else "적용", None if done else (lambda: self._chip_apply(key, strategy)))]
        chips += [Chip(STRATEGY_LABELS[st], lambda st=st: self._chip_mask(key, st), kind="ghost") for st in options]
        title = f"{TYPE_LABELS[it.span.type]} · 마스킹" + (f" · {STRATEGY_LABELS[strategy]}" if options else "")
        self.chip.show(title=title, back=lambda: self._chip_root(key), word=word, deleted=deleted, chips=chips)

    def _chip_apply(self, key, strategy: str) -> None:
        it = self._item(key)
        before = dict(self.decisions.get(key, {}))
        self._decide(it, applied=True, strategy=strategy)
        it = self._item(key)
        if strategy in ("delete", "block") or it is None or not it.replacement:
            message = "이 부분은 빼고 보내요" if it is not None and it.is_blocker else "지웠어요"
        else:
            message = f"{it.replacement}{ro(it.replacement)} 바꿨어요"
        self._chip_done(key, message, lambda: self._restore_decision(key, before))

    def _chip_reasons(self, key) -> None:
        it = self._item(key)
        if it is None or self.chip is None:
            return self._close_chip()
        note = "기밀로 판정된 내용이에요. 원문을 보내면 보안 검토 대상으로 기록돼요." if it.is_blocker else ""
        self.chip.show(title=f"{TYPE_LABELS[it.span.type]} · 원문유지 사유", back=lambda: self._chip_root(key),
                       note=note, chips=[Chip(name, lambda code=code: self._chip_keep(key, code))
                                         for code, name in REASONS])

    def _chip_keep(self, key, code: str) -> None:
        it = self._item(key)
        before = dict(self.decisions.get(key, {}))
        if code == "false_positive" and not it.is_blocker:
            self._ignore(it)  # 오탐: 이 값은 앞으로 탐지하지 않는다 (메모리)
            undo = lambda: self._unignore(it.span)  # noqa: E731
        else:
            self._decide(it, applied=False, reason=code)
            undo = lambda: self._restore_decision(key, before)  # noqa: E731
        self._chip_done(key, f"원문을 유지해요 · {REASON_SHORT[code]}", undo)

    def _chip_done(self, key, message: str, undo) -> None:
        if self.chip is None:
            return
        label = TYPE_LABELS.get(key[0], "")

        def run_undo():
            undo()
            self._close_chip()

        self.chip.show(title=label, message=message, chips=[Chip("되돌리기", run_undo, kind="ghost", icon="undo")])

    def _restore_decision(self, key, before: dict) -> None:
        if before:
            self.decisions[key] = before
        else:
            self.decisions.pop(key, None)
        self.refresh()

    def _unignore(self, span) -> None:
        self.app.ignore.discard(span.value)
        if span.type in self.ignored_types:
            self.ignored_types.remove(span.type)
        self.refresh()

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
        """원문 그대로 사용: 대화상자 대신 버튼 위에 사유 칩 셋을 펼친다."""
        btn = self.btn_original
        panel = self._chip_at(btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height())
        blocked = self.grade == "C"
        note = ("기밀로 판정된 내용이에요. 외부 AI에 보내면 보안 검토 대상으로 기록돼요." if blocked
                else "바꾸지 않은 원문을 복사해요. 원문은 기록하지 않고 유형·건수·사유만 이 PC에 남아요.")
        panel.anchor = (btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height(), btn.winfo_rooty())
        panel.show(title="원문 그대로 사용 · 사유", note=note, note_tone="warning" if blocked else "info",
                   chips=[Chip(name, lambda code=code: self._send_original(code)) for code, name in REASONS])

    def _send_original(self, reason: str) -> None:
        self._close_chip()
        self.app.copy_to_clipboard(self.orig.get("1.0", "end-1c"))
        queue = self._log("sent_original", reason=reason)
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
        self._close_chip()
        self.app.review_closed(self)
        self.destroy()
