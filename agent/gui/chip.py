"""하이라이트 칩: 입력창 위에 떠서 한 단계씩 펼쳐지는 작은 패널.

모양(디자인 설정): 패널 면 #25292D + 선 #3A4148(그림자 대신), 모서리 12, 안쪽 여백 12.
- 머리: (뒤로) + 유형·단계 이름 12/500 회색
- 바뀔 값: 변환된 값 표시(칩 바탕 + 15/500). 지우는 값은 일부만 보이고 취소선
- 알림 줄: 상태 아이콘 + 13/500
- 칩: 알약(높이 32). 고르는 칩은 모두 같은 모양이라 색으로 답을 암시하지 않는다
창을 새로 열지 않고 이 패널 안에서 다음 칩을 펼친다.
"""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont

from . import theme

MAX_WIDTH = 360


class Chip:
    """칩 하나: (글자, 누르면 할 일, 켜짐)"""

    def __init__(self, text: str, command=None, enabled: bool = True, kind: str = "chip", icon: str | None = None):
        self.text, self.command, self.enabled, self.kind, self.icon = text, command, enabled, kind, icon


class ChipPanel:
    def __init__(self, master, x: int, y: int, bounds, below: int | None = None, no_activate=None, on_show=None):
        """(x, y): 하이라이트 아래 왼쪽. below: 위로 펼칠 때 기준(하이라이트 위쪽 y). bounds: 그 모니터의 작업 영역.
        no_activate(창): 눌러도 포커스를 가져가지 않게 (원래 입력창 칩). on_show(): 처음 보인 뒤 한 번."""
        self.master = master
        self.anchor = (x, y, below if below is not None else y)
        self.bounds = bounds
        self.on_show = on_show
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        if no_activate:
            try:  # 보이기 전에 정한다: 처음 보일 때도 입력창의 포커스를 가져가지 않게
                self.win.update_idletasks()
                no_activate(self.win)
            except Exception:  # 포커스를 가져가도 동작은 같다 (바꿀 때 입력창을 다시 앞으로)
                pass
        key = theme.BG
        if sys.platform == "win32":
            try:
                self.win.attributes("-transparentcolor", "#010203")
                key = "#010203"
            except tk.TclError:
                pass
        self.win.configure(bg=key)
        self.canvas = tk.Canvas(self.win, highlightthickness=0, bd=0, bg=key)
        self.canvas.pack()
        self.body = tk.Frame(self.canvas, bg=theme.SURFACE)
        self.pad = theme.px(12)
        self._item = self.canvas.create_window(self.pad, self.pad, window=self.body, anchor="nw")
        self.shown = False
        self.buttons: list[theme.Button] = []
        self.win.bind("<Escape>", lambda e: self.destroy())

    # ------------------------------------------------------------------ 그리기
    def show(self, *, title: str = "", back=None, note: str = "", note_tone: str = "warning", word: str | None = None,
             deleted: bool = False, message: str = "", tone: str = "success", chips=()) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        self.buttons = []
        px = theme.px
        if title or back:
            head = tk.Frame(self.body, bg=theme.SURFACE)
            head.pack(fill="x", pady=(0, px(8)))
            if back:
                b = theme.Button(head, "", back, kind="ghost", size="xs", icon="back")
                b.pack(side="left", padx=(0, px(4)))
                self.buttons.append(b)
            tk.Label(head, text=title, font=theme.F["t12m"], bg=theme.SURFACE, fg=theme.MUTE).pack(side="left")
        if note:
            self._line(note, note_tone, theme.F["t12m"])
        if word is not None:
            self._word(word, deleted)
        if message:
            self._line(message, tone, theme.F["t13m"])
        if chips:
            self._chips(chips)
        self._layout()

    def _line(self, text: str, tone: str, font) -> None:
        row = tk.Frame(self.body, bg=theme.SURFACE)
        row.pack(fill="x", pady=(0, theme.px(8)))
        icon = {"success": "success", "warning": "warning", "danger": "danger", "error": "danger"}.get(tone, "info")
        theme.Icon(row, icon, theme.TONE_COLOR.get(tone, theme.SUB)).pack(side="left", anchor="n", pady=(theme.px(1), 0))
        tk.Label(row, text=text, font=font, bg=theme.SURFACE, fg=theme.FG, justify="left", anchor="w",
                 wraplength=theme.px(MAX_WIDTH - 40)).pack(side="left", padx=(theme.px(6), 0))

    def _word(self, word: str, deleted: bool) -> None:
        """바뀔 값만 보여 준다: 변환된 값은 칩 바탕, 지우는 값은 취소선."""
        px = theme.px
        font = theme.F["t15m"]
        if deleted:
            base = tkfont.Font(font=font).actual()
            font = (base["family"], -px(15), "overstrike")
        width = min(tkfont.Font(font=font).measure(word), px(MAX_WIDTH - 40)) + px(16)
        height = tkfont.Font(font=font).metrics("linespace") + px(8)
        c = tk.Canvas(self.body, width=width, height=height, highlightthickness=0, bd=0, bg=theme.SURFACE)
        c.pack(anchor="w", pady=(0, px(12)))
        if not deleted:
            theme.rounded_rect(c, 0, 0, width - 1, height - 1, px(4), fill=theme.CHIP, outline=theme.CHIP)
        c.create_text(px(8), height / 2, text=word, anchor="w", font=font,
                      fill=theme.MUTE if deleted else theme.FG, width=width - px(16))
        self.word = c

    def _chips(self, chips) -> None:
        px = theme.px
        row = tk.Frame(self.body, bg=theme.SURFACE)
        row.pack(fill="x")
        used = 0
        for chip in chips:
            b = theme.Button(row, chip.text, chip.command, kind=chip.kind, size="sm", icon=chip.icon)
            if not chip.enabled:
                b.configure(state="disabled")
            need = int(b.cget("width")) + px(6)
            if used and used + need > px(MAX_WIDTH):
                row = tk.Frame(self.body, bg=theme.SURFACE)
                row.pack(fill="x", pady=(px(6), 0))
                b.destroy()
                b = theme.Button(row, chip.text, chip.command, kind=chip.kind, size="sm", icon=chip.icon)
                if not chip.enabled:
                    b.configure(state="disabled")
                used = 0
            b.pack(side="left", padx=(0, px(6)))
            used += need
            self.buttons.append(b)

    def _layout(self) -> None:
        self.body.update_idletasks()
        w = self.body.winfo_reqwidth() + 2 * self.pad
        h = self.body.winfo_reqheight() + 2 * self.pad
        self.canvas.configure(width=w, height=h)
        self.canvas.delete("shape")
        theme.rounded_rect(self.canvas, 0.5, 0.5, w - 1.5, h - 1.5, theme.px(12), fill=theme.SURFACE,
                           outline=theme.LINE, tags="shape")
        self.canvas.tag_lower("shape")
        x, y, top = self.anchor
        left, upper, right, bottom = self.bounds
        gap = theme.px(8)
        px_ = max(left, min(x, right - w))
        py = y + gap if y + gap + h <= bottom else max(upper, top - h - gap)
        self.win.geometry(f"+{int(px_)}+{int(py)}")
        if not self.shown:
            self.shown = True
            self.win.deiconify()
            if self.on_show:
                try:
                    self.on_show()
                except Exception:
                    pass
        self.win.lift()

    # ------------------------------------------------------------------ 상태
    def contains(self, x: int, y: int) -> bool:
        try:
            left, top = self.win.winfo_rootx(), self.win.winfo_rooty()
            return left <= x < left + self.win.winfo_width() and top <= y < top + self.win.winfo_height()
        except tk.TclError:
            return False

    def chip_labels(self) -> list[str]:
        return [b.cget("text") for b in self.buttons if b.cget("text")]

    def texts(self) -> list[str]:
        """패널에 보이는 글자 (테스트용): 이름·알림 줄·바뀔 값."""
        out = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    out.append(child.cget("text"))
                elif isinstance(child, tk.Canvas) and not isinstance(child, theme.Button):
                    out.extend(child.itemcget(i, "text") for i in child.find_all() if child.type(i) == "text")
                walk(child)

        walk(self.body)
        return out

    def press(self, text: str) -> None:
        """테스트·키보드용: 글자가 같은 칩을 누른다."""
        for b in self.buttons:
            if b.cget("text") == text:
                b.invoke()
                return
        raise KeyError(text)

    def exists(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
