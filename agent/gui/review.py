"""점검 창 (§7 변환 미리보기의 데스크톱판).

흐름: 원문 ↔ 변환문 → 하이라이트/표 클릭 → 사용자 행동(되돌리기+사유 / 다시 가리기 / 방식 변경 / 오탐 등록)
     → [변환문 복사] / [취소] / [원문 그대로 사용(사유)] → 큐 분류 로그
"""
from __future__ import annotations

import tkinter as tk
from collections import Counter
from tkinter import ttk

from ..detect.pipeline import COVERAGE_LABELS, detect
from ..detect.scoring import overall_grade
from ..detect.span import CATEGORY, TYPE_LABELS
from ..sanitize.engine import ALLOWED, STRATEGY_LABELS, make_items, sanitize
from ..system import win32
from . import theme

REASONS = [("false_positive", "오탐이에요 (개인정보가 아님)"), ("business_need", "업무상 꼭 필요해요"),
           ("approved", "승인받았어요"), ("none", "선택 안 함")]
REASON_SHORT = {"false_positive": "오탐", "business_need": "업무상 필요", "approved": "승인받음", "none": "사유 없음"}
MISSED_TYPES = [("name", "이름"), ("phone", "전화번호"), ("address", "주소"), ("other", "기타")]
HINT = "하이라이트를 클릭하면 되돌리거나 변환 방식을 바꿀 수 있어요 · 놓친 정보는 드래그 후 우클릭"


def _short(value: str, n: int = 24) -> str:
    v = value.replace("\n", " ⏎ ")
    return v if len(v) <= n else v[: n - 1] + "…"


def shape_of(value: str) -> str:
    """미탐 신고용: 값 대신 형태만 남긴다 (예: '숫자13 · 총 14자')."""
    digits = sum(c.isdigit() for c in value)
    hangul = sum("가" <= c <= "힣" for c in value)
    latin = sum(c.isascii() and c.isalpha() for c in value)
    parts = [f"{k}{n}" for k, n in (("숫자", digits), ("한글", hangul), ("영문", latin)) if n]
    return " ".join(parts) + f" · 총 {len(value)}자"


class Tooltip:
    def __init__(self, master):
        self.master = master
        self.win = None

    def show(self, x: int, y: int, text: str) -> None:
        self.hide()
        self.win = tk.Toplevel(self.master)
        self.win.wm_overrideredirect(True)
        self.win.attributes("-topmost", True)
        tk.Label(self.win, text=text, justify="left", bg="#111827", fg="white", font=theme.SMALL,
                 padx=8, pady=5, wraplength=380).pack()
        self.win.geometry(f"+{x + 14}+{y + 18}")

    def hide(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None


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

        self.title("AI 전송 전 점검 — KP Guard")
        self.geometry("1120x740")
        self.minsize(880, 580)
        self.configure(bg=theme.BG)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self._build()
        if text:
            self.orig.insert("1.0", text)
        self.orig.edit_modified(False)
        self.refresh()
        self._bring_front()

    # ================================================================== UI 구성
    def _build(self) -> None:
        self.banner = tk.Frame(self, padx=16, pady=10)
        self.banner.pack(fill="x")
        self.b_title = tk.Label(self.banner, font=theme.TITLE, anchor="w")
        self.b_title.pack(fill="x")
        self.b_detail = tk.Label(self.banner, font=theme.FONT, anchor="w", justify="left", wraplength=1050)
        self.b_detail.pack(fill="x")
        self.b_actions = tk.Frame(self.banner)
        self.btn_internal = theme.plain_button(self.b_actions, "사내 AI에서 처리", self.on_internal)
        self.btn_internal.pack(side="left", pady=(6, 0))

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(10, 4))
        left = ttk.LabelFrame(body, text=" 원문 · 직접 입력하거나 수정할 수 있어요 ", padding=6)
        right = ttk.LabelFrame(body, text=" AI에 보낼 변환문 ", padding=6)
        body.add(left, weight=1)
        body.add(right, weight=1)

        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(0, 4))
        theme.plain_button(bar, "클립보드에서 불러오기", self._load_clipboard, small=True).pack(side="left")
        theme.plain_button(bar, "모두 지우기", lambda: self.orig.delete("1.0", "end"), small=True).pack(side="left", padx=4)
        self.orig = theme.scrolled_text(left, undo=True)
        self.orig.bind("<<Modified>>", self._on_modified)
        self.orig.bind("<Button-3>", self._on_right_click)

        rbar = ttk.Frame(right)
        rbar.pack(fill="x", pady=(0, 4))
        self.out_info = tk.Label(rbar, text="", font=theme.SMALL, fg=theme.MUTED, bg=theme.BG)
        self.out_info.pack(side="left")
        self.cov_info = tk.Label(rbar, text="", font=theme.SMALL, fg=theme.MUTED, bg=theme.BG)
        self.cov_info.pack(side="right")
        self.out = theme.scrolled_text(right, state="disabled", cursor="arrow")

        table = ttk.LabelFrame(self, text=" 탐지 항목 · 행을 클릭해도 같은 메뉴가 열려요 ", padding=6)
        table.pack(fill="x", padx=12, pady=4)
        cols = (("type", "유형", 120), ("orig", "원문", 210), ("repl", "변환", 190),
                ("strategy", "방식", 70), ("state", "상태", 130), ("why", "탐지 근거", 330))
        self.tree = ttk.Treeview(table, columns=[c[0] for c in cols], show="headings", height=6, selectmode="none")
        for cid, label, width in cols:
            self.tree.heading(cid, text=label)
            self.tree.column(cid, width=width, anchor="w", stretch=cid == "why")
        self.tree.tag_configure("reverted", foreground=theme.MUTED)
        self.tree.tag_configure("suspect", foreground=theme.SUSPECT_FG)
        self.tree.tag_configure("blocker", foreground="#b71c1c")
        self.tree.pack(fill="x")
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)

        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="x", padx=12, pady=(6, 12))
        tk.Label(bottom, text=HINT, font=theme.SMALL, fg=theme.MUTED, bg=theme.BG).pack(side="left")
        self.btn_send = theme.primary_button(bottom, "변환문 복사", self.on_send)
        self.btn_send.pack(side="right")
        theme.plain_button(bottom, "취소", self.on_cancel).pack(side="right", padx=8)
        tk.Button(bottom, text="원문 그대로 사용", command=self.on_original, relief="flat", bg=theme.BG,
                  fg=theme.MUTED, font=theme.SMALL, cursor="hand2").pack(side="right", padx=8)

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
    def _style_for(self, it) -> dict:
        if it.is_blocker:
            return {"background": theme.HIGHLIGHT["blocker"] if it.applied else theme.REVERTED_BG}
        if it.applied:
            return {"background": theme.HIGHLIGHT[CATEGORY[it.span.type]]}
        if it.revert_reason:
            return {"background": theme.REVERTED_BG, "underline": True}
        return {"background": theme.SUSPECT_BG, "foreground": theme.SUSPECT_FG, "underline": True}

    def _bind_tag(self, widget: tk.Text, tag: str, idx: int) -> None:
        widget.tag_bind(tag, "<ButtonRelease-1>", lambda e, i=idx, w=widget: self._on_tag_click(e, i, w))
        widget.tag_bind(tag, "<Enter>", lambda e, i=idx: self._show_tip(e, i))
        widget.tag_bind(tag, "<Leave>", lambda e: self.tip.hide())
        widget.tag_configure(tag, **{"borderwidth": 0})

    def _clear_tags(self, widget: tk.Text) -> None:
        for tag in widget.tag_names():
            if tag.startswith("it"):
                widget.tag_delete(tag)

    def _paint_original(self) -> None:
        self._clear_tags(self.orig)
        for i, it in enumerate(self.items):
            tag = f"it{i}"
            self.orig.tag_add(tag, theme.index(it.span.start), theme.index(it.span.end))
            self.orig.tag_configure(tag, **self._style_for(it))
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
            self.out.tag_configure(tag, **self._style_for(it))
            self._bind_tag(self.out, tag, i)
        self.out.configure(state="disabled")
        applied = sum(1 for it in self.items if it.applied and not it.is_blocker)
        deleted = sum(1 for it in self.items if it.applied and it.strategy == "delete")
        self.out_info.configure(text=f"변환 {applied}건 (삭제 {deleted}건은 변환문에 표시되지 않아요)" if applied else "")
        self.cov_info.configure(**self._coverage_label())

    def _coverage_label(self) -> dict:
        """어느 단계까지 검사했는지 (모델이 꺼져 있거나 실패하면 그대로 알린다)."""
        if self.coverage == "model_failed":
            return {"text": "검사: 규칙·사전만 · 소형 모델 실패", "fg": theme.SUSPECT_FG}
        if self.coverage == "model_skipped":
            return {"text": "검사: 규칙·사전만 · 글이 길어 소형 모델 생략", "fg": theme.SUSPECT_FG}
        if self.coverage == "rules" and self.app.models.state == "loading":
            return {"text": "검사: 규칙·사전 · 소형 모델 준비 중", "fg": theme.MUTED}
        return {"text": f"검사: {COVERAGE_LABELS[self.coverage]}", "fg": theme.MUTED}

    def _state_text(self, it) -> tuple[str, str]:
        if it.is_blocker:
            if not it.applied:
                return f"차단 해제 · {REASON_SHORT.get(it.revert_reason, '')}", "reverted"
            return ("빼고 보냄" if it.strategy == "delete" else "차단"), "blocker"
        if it.applied:
            return "적용", ""
        if it.revert_reason:
            return f"되돌림 · {REASON_SHORT[it.revert_reason]}", "reverted"
        return "확인 필요 (클릭)", "suspect"

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
            self.tree.insert("", "end", iid=str(i), tags=(tag,), values=(
                TYPE_LABELS[s.type], _short(s.value), _short(repl), STRATEGY_LABELS[it.strategy], state,
                f"{s.reason} · 신뢰도 {s.confidence:.2f}"))

    def _update_banner(self) -> None:
        bg, fg = theme.GRADE[self.grade]
        for w in (self.banner, self.b_title, self.b_detail, self.b_actions):
            w.configure(bg=bg)
        self.b_title.configure(fg=fg)
        self.b_detail.configure(fg=fg)
        self.b_actions.pack_forget()
        normal = [it for it in self.items if not it.is_blocker]
        applied = sum(1 for it in normal if it.applied)
        suspects = sum(1 for it in normal if it.suspect)
        reverted = sum(1 for it in normal if it.revert_reason)
        lines = []
        if self.grade == "C":
            self.b_title.configure(text="기밀 · 외부 AI 전송이 차단됐어요")
            for it in self.items:
                if it.is_blocker and it.applied and it.strategy == "block":
                    lines.append(f"• {it.span.reason}")
            lines.append("→ 해당 부분을 클릭해 '이 부분 빼고 보내기'를 고르면 외부 AI에 보낼 수 있어요. "
                         "그대로 써야 하면 사내 AI를 이용하세요.")
            lines += [f"• {n}" for n in self.notes]
            self.b_actions.pack(fill="x")
            self.btn_send.configure(text="외부 전송 차단됨", state="disabled")
        elif self.grade == "S":
            self.b_title.configure(text=f"민감 · 개인정보 {applied}건을 안전한 값으로 바꿨어요")
            if suspects:
                lines.append(f"• 확인이 필요한 항목 {suspects}건(밑줄)은 아직 바꾸지 않았어요. 클릭해서 가릴 수 있어요.")
            if reverted:
                lines.append(f"• 되돌린 항목 {reverted}건은 원문 그대로 나가요.")
            lines += [f"• {n}" for n in self.notes]
            lines.append("변환문을 복사해 Claude Code·Codex·채팅창에 붙여넣으세요. AI 답변은 '답변 복원'으로 원래 값으로 바꿀 수 있어요.")
            self.btn_send.configure(text="변환문 복사  (Ctrl+Enter)", state="normal")
        else:
            self.b_title.configure(text="공개 · 민감정보가 보이지 않아요")
            lines += [f"• {n}" for n in self.notes]
            lines.append("그대로 보내도 됩니다. 놓친 정보가 있으면 드래그 후 우클릭으로 가릴 수 있어요.")
            self.btn_send.configure(text="그대로 복사  (Ctrl+Enter)", state="normal")
        self.b_detail.configure(text="\n".join(lines))

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
        sub = tk.Menu(parent, tearoff=0)
        for code, label in REASONS:
            sub.add_command(label=label, command=lambda c=code: self._decide(it, applied=False, reason=c))
        return sub

    def _open_menu(self, idx: int, x: int, y: int) -> None:
        self.tip.hide()
        if idx >= len(self.items):
            return
        it = self.items[idx]
        s = it.span
        m = tk.Menu(self, tearoff=0)
        head = f"{TYPE_LABELS[s.type]} · {_short(s.value, 30)}"
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
            strategies = tk.Menu(m, tearoff=0)
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
        m = tk.Menu(self, tearoff=0)
        try:
            selected = self.orig.get("sel.first", "sel.last")
        except tk.TclError:
            selected = ""
        if selected.strip():
            sub = tk.Menu(m, tearoff=0)
            for typ, label in MISSED_TYPES:
                sub.add_command(label=label, command=lambda t=typ, v=selected.strip(): self._report_missed(t, v))
            m.add_cascade(label=f"이것도 가려줘 · 놓친 정보 신고  “{_short(selected.strip(), 16)}”", menu=sub)
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
        msg = ("변환문을 복사했어요. Claude Code·Codex·채팅창에 붙여넣으세요 (Ctrl+V)."
               if self.grade == "S" else "복사했어요. 붙여넣어 사용하세요.")
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
        self.app.toast("[데모] 사내 AI로 안내합니다. 원문을 복사했으니 사내 AI 입력창에 붙여넣으세요.")
        self._close()

    def _load_clipboard(self) -> None:
        text = win32.get_clipboard_text()
        if text:
            self.orig.delete("1.0", "end")
            self.orig.insert("1.0", text)

    def _close(self) -> None:
        self.tip.hide()
        self.app.review_closed(self)
        self.destroy()


class ReasonDialog(tk.Toplevel):
    """원문 전송 시 사유 선택. 사유는 큐 분류에만 쓰인다."""

    def __init__(self, master, blocked: bool):
        super().__init__(master)
        self.result = None
        self.title("원문 그대로 사용")
        self.configure(bg="white", padx=20, pady=16)
        self.resizable(False, False)
        self.transient(master)
        msg = ("기밀로 판정된 내용이에요. 외부 AI에 보내면 보안 검토 대상으로 기록돼요."
               if blocked else "바꾸지 않은 원문을 복사합니다. 이유를 알려주세요.")
        tk.Label(self, text=msg, bg="white", font=theme.BOLD, wraplength=360, justify="left").pack(anchor="w")
        tk.Label(self, text="원문은 기록되지 않고, 유형·건수·사유만 이 PC에 남아요.", bg="white",
                 fg=theme.MUTED, font=theme.SMALL).pack(anchor="w", pady=(2, 10))
        self.var = tk.StringVar(value="none")
        for code, label in REASONS:
            tk.Radiobutton(self, text=label, value=code, variable=self.var, bg="white",
                           anchor="w").pack(fill="x")
        row = tk.Frame(self, bg="white")
        row.pack(fill="x", pady=(14, 0))
        theme.primary_button(row, "원문 복사", self._ok).pack(side="right")
        theme.plain_button(row, "돌아가기", self.destroy).pack(side="right", padx=8)
        self.grab_set()
        self.geometry(f"+{master.winfo_rootx() + 300}+{master.winfo_rooty() + 200}")

    def _ok(self) -> None:
        self.result = self.var.get()
        self.destroy()
