"""답변 복원 창 (§6): AI 답변의 가짜 값을 이 PC 메모리의 매핑으로 원래 값으로 바꾼다."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..sanitize.restore import restore
from ..system import win32
from . import theme


def build_restore_layout(win, left_title: str, note: str) -> None:
    """복원 창 공통 배치: 배너 / [AI 답변 카드 | 복원 결과 카드] / 하단(안내 왼쪽, 버튼 오른쪽).

    win에 banner, info, src, dst, bottom 을 만든다. 복원된 부분은 초록으로 표시한다.
    """
    gap = theme.px(16)
    win.banner = theme.Banner(win)
    win.banner.pack(fill="x", padx=gap, pady=(gap, 0))
    win.info = win.banner.detail
    win.bottom = tk.Frame(win, bg=theme.BG_CANVAS)
    win.bottom.pack(side="bottom", fill="x", padx=gap, pady=(theme.px(8), gap))
    theme.label(win.bottom, note).pack(side="left")
    body = ttk.PanedWindow(win, orient="horizontal")
    body.pack(fill="both", expand=True, padx=gap, pady=(theme.px(12), 0))
    left = theme.Card(body, title=left_title)
    right = theme.Card(body, title="복원 결과", caption="초록 = 원래 값으로 되돌린 부분")
    body.add(left, weight=1)
    body.add(right, weight=1)
    theme.Button(left.actions, "클립보드에서 불러오기", win._paste, kind="ghost", size="sm").pack(side="right")
    win.src = theme.scrolled_text(left.body, undo=True)
    win.dst = theme.scrolled_text(right.body, state="disabled")
    win.dst.tag_configure("restored", **theme.RESTORED)


class RestoreWindow(tk.Toplevel):
    def __init__(self, app, text: str = ""):
        super().__init__(app.root)
        self.app = app
        self._job = None
        self.result = ""
        theme.window(self, "답변 복원 — KP Guard", 1080, 680, 820, 520)
        self.protocol("WM_DELETE_WINDOW", self._close)
        build_restore_layout(self, "AI 답변 (가짜 값 포함)",
                             "매핑은 이 PC 메모리에만 있어요. 앱을 끄거나 '매핑 초기화'를 누르면 사라져요")
        theme.Button(self.bottom, "복원 결과 복사", self._copy, kind="primary").pack(side="right")
        theme.Button(self.bottom, "닫기", self._close).pack(side="right", padx=theme.px(8))
        self.src.bind("<<Modified>>", self._on_modified)
        if text:
            self.src.insert("1.0", text)
        self.src.edit_modified(False)
        self.run()
        self.lift()
        self.focus_force()

    def _on_modified(self, _e=None) -> None:
        if not self.src.edit_modified():
            return
        self.src.edit_modified(False)
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(300, self.run)  # 스트리밍처럼 계속 바뀌어도 멈출 때마다 재치환

    def run(self) -> None:
        self._job = None
        text, ranges = restore(self.src.get("1.0", "end-1c"), self.app.session.entries)
        self.result = text
        self.dst.configure(state="normal")
        self.dst.delete("1.0", "end")
        self.dst.insert("1.0", text)
        for s, e in ranges:
            self.dst.tag_add("restored", theme.index(s), theme.index(e))
        self.dst.configure(state="disabled")
        n = len(self.app.session.entries)
        if not n:
            self.banner.set("neutral", "AI 답변 복원", "아직 매핑이 없어요. 먼저 점검 창에서 변환문을 만들어 보세요.")
        elif ranges:
            self.banner.set("success", f"원래 값 {len(ranges)}곳을 되돌렸어요", f"현재 매핑 {n}개")
        else:
            self.banner.set("neutral", "AI 답변 복원", f"현재 매핑 {n}개 · 이 답변에는 되돌릴 가짜 값이 없어요")

    def _paste(self) -> None:
        text = win32.get_clipboard_text()
        if text:
            self.src.delete("1.0", "end")
            self.src.insert("1.0", text)

    def _copy(self) -> None:
        self.app.copy_to_clipboard(self.result)
        self.app.toast("복원 결과를 복사했어요")

    def _close(self) -> None:
        self.app.restore_closed(self)
        self.destroy()
