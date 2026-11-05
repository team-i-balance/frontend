"""답변 복원 창 (§6): AI 답변의 가짜 값을 이 PC 메모리의 매핑으로 원래 값으로 바꾼다."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..sanitize.restore import restore
from ..system import win32
from . import theme


class RestoreWindow(tk.Toplevel):
    def __init__(self, app, text: str = ""):
        super().__init__(app.root)
        self.app = app
        self._job = None
        self.title("답변 복원 — KP Guard")
        self.geometry("980x600")
        self.configure(bg=theme.BG)
        self.protocol("WM_DELETE_WINDOW", self._close)

        head = tk.Frame(self, bg="#eef2ff", padx=16, pady=10)
        head.pack(fill="x")
        tk.Label(head, text="AI 답변 복원", font=theme.TITLE, bg="#eef2ff", fg="#1e3a8a", anchor="w").pack(fill="x")
        self.info = tk.Label(head, bg="#eef2ff", fg="#1e3a8a", anchor="w", justify="left")
        self.info.pack(fill="x")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=10)
        left = ttk.LabelFrame(body, text=" AI 답변 (가짜 값 포함) ", padding=6)
        right = ttk.LabelFrame(body, text=" 복원 결과 · 파란 밑줄 = 원래 값으로 되돌린 부분 ", padding=6)
        body.add(left, weight=1)
        body.add(right, weight=1)
        theme.plain_button(left, "클립보드에서 불러오기", self._paste, small=True).pack(anchor="w", pady=(0, 4))
        self.src = theme.scrolled_text(left, undo=True)
        self.src.bind("<<Modified>>", self._on_modified)
        tk.Frame(right, height=26).pack()
        self.dst = theme.scrolled_text(right, state="disabled")
        self.dst.tag_configure("restored", foreground=theme.RESTORED_FG, underline=True)

        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(bottom, text="매핑은 이 PC 메모리에만 있고 앱을 끄거나 '매핑 초기화'를 누르면 사라져요.",
                 font=theme.SMALL, fg=theme.MUTED, bg=theme.BG).pack(side="left")
        theme.primary_button(bottom, "복원 결과 복사", self._copy).pack(side="right")
        theme.plain_button(bottom, "닫기", self._close).pack(side="right", padx=8)

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
        self.info.configure(text=f"현재 매핑 {n}개 · 이번 답변에서 {len(ranges)}곳 복원" if n
                            else "아직 매핑이 없어요. 먼저 점검 창에서 변환문을 만들어 보세요.")

    def _paste(self) -> None:
        text = win32.get_clipboard_text()
        if text:
            self.src.delete("1.0", "end")
            self.src.insert("1.0", text)

    def _copy(self) -> None:
        self.app.copy_to_clipboard(self.result)
        self.app.toast("복원 결과를 복사했어요.")

    def _close(self) -> None:
        self.app.restore_closed(self)
        self.destroy()
