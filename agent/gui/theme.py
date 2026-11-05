"""다크 디자인 시스템을 tkinter로 옮긴 것 (보안팀 콘솔 v17과 같은 토큰).

- 거의 검정 바탕 위에 아웃라인으로 경계를 만든다. 떠 있는 표면(대화상자·툴팁)만 한 단계 밝힌다.
- 앰버(#F6A623)는 '지금 처리할 한 곳'에만 쓴다: 창마다 주요 버튼 하나, 토스트. 선택 표시는 회색(bg/selected).
- 빨강·초록·노랑은 오류·성공·경고 상태에만 쓴다. 색만으로 상태를 전하지 않고 문구를 함께 쓴다.
- 글꼴은 Pretendard(앱에 동봉, agent/assets/fonts, OFL). Windows에서는 이 프로세스에만 등록해 쓴다.
- tkinter가 못 하는 것: 자간·tabular-nums·그림자·알파. 반투명 토큰은 놓일 바탕에 미리 섞어 둔다.
  둥근 모서리는 Canvas로 그린다. 크기는 디자인의 px 값을 화면 배율(DPI)에 맞춰 키운다.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"


# ====================================================================== 색 원색·의미 토큰
def blend(rgb: tuple[int, int, int], alpha: float, base: str) -> str:
    """rgba(r,g,b,alpha)를 base 위에 섞은 불투명 색."""
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(alpha * c + (1 - alpha) * d):02X}" for c, d in zip(rgb, b))


GRAY = {950: "#131416", 900: "#1B1C20", 850: "#212227", 800: "#222327", 750: "#2A2B30", 700: "#2B2C31",
        650: "#303137", 600: "#33343A", 500: "#4A4C54", 400: "#6F727C", 350: "#8A8D96", 300: "#A7A9B1",
        200: "#C9CBD2", 50: "#F1F1F3"}
AMBER_500, AMBER_300, AMBER_INK = "#F6A623", "#FFC55C", "#1A1000"
RED_300, GREEN_300 = "#FF8A80", "#5BD38A"

BG_CANVAS = GRAY[950]      # 화면 배경
BG_SURFACE = GRAY[900]     # 카드·패널 표면
BG_RAISED = GRAY[850]      # 대화상자·툴팁처럼 떠 있는 표면
BG_SUBTLE = GRAY[800]      # 보조 버튼 면, hover
BG_SELECTED = GRAY[700]    # 선택(앰버 아님)
BG_NEUTRAL = GRAY[750]     # 중립 배지, 보조 버튼 hover
BG_PRIMARY = AMBER_500
BG_PRIMARY_PRESSED = AMBER_300
TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY, TEXT_DISABLED = GRAY[50], GRAY[300], GRAY[350], GRAY[400]
TEXT_ON_PRIMARY, TEXT_ACCENT = AMBER_INK, AMBER_500
TEXT_DANGER, TEXT_SUCCESS, TEXT_WARNING = RED_300, GREEN_300, AMBER_300
BORDER_DEFAULT, BORDER_STRONG, BORDER_FOCUS = GRAY[650], GRAY[500], AMBER_500
BORDER_DANGER, BORDER_DISABLED = RED_300, GRAY[600]
SWITCH_OFF_TRACK = "#26272C"

_DANGER, _SUCCESS, _WARNING = ((255, 107, 107), .13), ((91, 211, 138), .12), ((246, 166, 35), .14)


def subtle(tone: str, on: str = BG_SURFACE) -> str:
    """bg/danger·success·warning-subtle을 바탕색 on 위에 섞은 값."""
    rgb, alpha = {"danger": _DANGER, "success": _SUCCESS, "warning": _WARNING}[tone]
    return blend(rgb, alpha, on)


# 상태 톤 (배경은 화면 배경 위에 놓이는 배너 기준): (면, 글자)
TONES = {
    "danger": (subtle("danger", BG_CANVAS), TEXT_DANGER),
    "success": (subtle("success", BG_CANVAS), TEXT_SUCCESS),
    "warning": (subtle("warning", BG_CANVAS), TEXT_WARNING),
    "neutral": (BG_SURFACE, TEXT_PRIMARY),
}
GRADE_TONE = {"O": "success", "S": "warning", "C": "danger"}

# 글 안 강조 (Text 태그. 글 상자 바탕 bg/surface 위)
MARK = {"background": subtle("danger"), "foreground": TEXT_DANGER}            # 가린 원문
SUSPECT = {"background": subtle("warning"), "foreground": TEXT_WARNING, "underline": True}  # 확인 필요
KEPT = {"background": BG_NEUTRAL, "foreground": TEXT_SECONDARY, "underline": True}         # 되돌려 원문 유지
SAFE = {"background": subtle("success"), "foreground": TEXT_SUCCESS}           # 바꾼 안전한 값
RAW = {"background": subtle("danger"), "foreground": TEXT_DANGER, "underline": True}       # 원문 노출
BLOCKED = {"background": subtle("danger"), "foreground": TEXT_DANGER}
RESTORED = {"background": subtle("success"), "foreground": TEXT_SUCCESS}

# ====================================================================== 글꼴·크기
SCALE = 1.0
FAMILY = "TkDefaultFont"
F: dict[str, tuple] = {}


def px(n: float) -> int:
    """디자인 px → 화면 픽셀 (DPI 배율 반영)."""
    return max(1, int(round(n * SCALE)))


def load_fonts() -> int:
    """동봉한 Pretendard를 이 프로세스에만 등록한다 (Windows). tk.Tk()를 만들기 전에 부른다."""
    if sys.platform != "win32" or not FONT_DIR.is_dir():
        return 0
    import ctypes
    added = 0
    for path in sorted(FONT_DIR.glob("*.otf")):
        try:
            added += ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0)  # FR_PRIVATE
        except (AttributeError, OSError):
            pass
    return added


def _fonts(root: tk.Misc) -> None:
    global FAMILY
    families = set(tkfont.families(root))
    pick = lambda *names: next((n for n in names if n in families), None)  # noqa: E731
    FAMILY = pick("Pretendard", "Pretendard Variable", "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo",
                  "NanumGothic", "Noto Sans CJK KR") or "TkDefaultFont"
    medium = pick("Pretendard Medium")
    semibold = pick("Pretendard SemiBold")
    code = pick("JetBrains Mono", "Cascadia Mono", "Consolas", "D2Coding", "DejaVu Sans Mono") or "TkFixedFont"

    def regular(size):
        return (FAMILY, -px(size))

    def med(size):
        return (medium, -px(size)) if medium else (FAMILY, -px(size))

    def semi(size):
        return (semibold, -px(size)) if semibold else (FAMILY, -px(size), "bold")

    F.update({
        "title32": (FAMILY, -px(32), "bold"), "title24": semi(24), "heading20": semi(20),
        "body16": med(16), "body16r": regular(16), "label14": med(14), "label14r": regular(14),
        "caption12": regular(12), "badge11": med(11), "code12": (code, -px(12)), "mark16": semi(16),
    })


# 예전 이름 (다른 창이 아직 쓰는 경우)
FONT = SMALL = BOLD = TITLE = TEXT = ("TkDefaultFont", 10)
BG, PRIMARY, MUTED = BG_CANVAS, BG_PRIMARY, TEXT_SECONDARY


def setup(root: tk.Tk) -> None:
    """창을 만들기 전에 한 번. 배율·글꼴·ttk 스타일·기본 색을 정한다."""
    global SCALE, FONT, SMALL, BOLD, TITLE, TEXT
    try:
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        SCALE = 1.0
    _fonts(root)
    FONT, SMALL, BOLD, TITLE, TEXT = F["body16r"], F["label14r"], F["body16"], F["title24"], F["body16r"]
    root.configure(bg=BG_CANVAS)
    root.option_add("*Font", F["body16r"])
    root.option_add("*Background", BG_CANVAS)
    root.option_add("*Foreground", TEXT_PRIMARY)
    root.option_add("*selectBackground", BORDER_STRONG)
    root.option_add("*selectForeground", TEXT_PRIMARY)

    style = ttk.Style(root)
    style.theme_use("clam")  # 기본 Windows 테마는 색을 바꾸지 못한다
    style.configure(".", background=BG_CANVAS, foreground=TEXT_PRIMARY, font=F["body16r"],
                    bordercolor=BORDER_DEFAULT, troughcolor=BG_SURFACE, focuscolor=BORDER_FOCUS)
    style.configure("Treeview", background=BG_SURFACE, fieldbackground=BG_SURFACE, foreground=TEXT_PRIMARY,
                    bordercolor=BG_SURFACE, lightcolor=BG_SURFACE, darkcolor=BG_SURFACE,
                    rowheight=px(40), font=F["body16r"], borderwidth=0)
    style.map("Treeview", background=[("selected", BG_SELECTED)], foreground=[("selected", TEXT_PRIMARY)])
    style.configure("Treeview.Heading", background=BG_SURFACE, foreground=TEXT_SECONDARY, font=F["label14"],
                    relief="flat", borderwidth=0, bordercolor=BORDER_DEFAULT, lightcolor=BG_SURFACE,
                    darkcolor=BG_SURFACE, padding=(px(12), px(8)))
    style.map("Treeview.Heading", background=[("active", BG_SURFACE)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # 바깥 테두리 없음
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", troughcolor=BG_SURFACE, background=BG_NEUTRAL,
                        bordercolor=BG_SURFACE, lightcolor=BG_NEUTRAL, darkcolor=BG_NEUTRAL,
                        arrowcolor=TEXT_SECONDARY, gripcount=0, arrowsize=px(12), relief="flat")
        style.map(f"{orient}.TScrollbar", background=[("active", GRAY[600])])
    style.configure("TPanedwindow", background=BG_CANVAS)
    style.configure("Sash", sashthickness=px(16), gripcount=0, background=BG_CANVAS,
                    lightcolor=BG_CANVAS, bordercolor=BG_CANVAS)
    style.configure("Dialog.TRadiobutton", background=BG_RAISED, foreground=TEXT_PRIMARY, font=F["body16r"],
                    indicatorbackground=BG_SURFACE, indicatorforeground=AMBER_500, indicatormargin=(0, 0, px(12), 0),
                    upperbordercolor=BORDER_STRONG, lowerbordercolor=BORDER_STRONG, focuscolor=BG_RAISED,
                    padding=(0, px(6)))
    style.map("Dialog.TRadiobutton", background=[("active", BG_RAISED)],
              indicatorbackground=[("selected", AMBER_500)], indicatorforeground=[("selected", AMBER_INK)])


def window(win: tk.Toplevel, title: str, width: int, height: int, minw: int | None = None,
           minh: int | None = None) -> None:
    """창 공통: 배경, 제목, 크기(배율 반영, 화면보다 크면 줄임)."""
    win.title(title)
    win.configure(bg=BG_CANVAS)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = min(px(width), sw - px(40)), min(px(height), sh - px(80))
    win.geometry(f"{w}x{h}")
    if minw and minh:
        win.minsize(min(px(minw), w), min(px(minh), h))


def compact(win: tk.Misc) -> bool:
    """높이 800px 이하 화면: 간격을 한 단계 줄여 주요 버튼이 첫 화면에 들어오게."""
    return win.winfo_screenheight() <= px(800)


def open_at_mark(review, start, item):
    if not review.winfo_exists():
        return
    position = index(start)
    review.orig.see(position)
    review.orig.update_idletasks()
    box = review.orig.bbox(position)
    if box:
        x, y, _, height = box
        review._open_menu(item, review.orig.winfo_rootx() + x, review.orig.winfo_rooty() + y + height)


def index(n: int) -> str:
    """파이썬 문자열 위치 → Tk Text 인덱스"""
    return f"1.0 + {n} chars"


def bg_of(widget: tk.Misc) -> str:
    try:
        return widget.cget("background")
    except (tk.TclError, AttributeError):
        return BG_CANVAS


def masked(value: str) -> str:
    """원문(개인정보)은 일부만 보여 준다: 앞뒤 일부 글자만 남기고 가운데를 *로."""
    chars = [i for i, c in enumerate(value) if c.isalnum()]
    if len(chars) <= 1:
        return "*" * len(value) if chars else value
    keep = max(1, len(chars) // 4)
    hidden = set(chars[keep:-keep]) if len(chars) > 2 else {chars[-1]}
    return "".join("*" if i in hidden else c for i, c in enumerate(value))


def short(value: str, n: int = 24) -> str:
    v = value.replace("\n", " ⏎ ")
    return v if len(v) <= n else v[: n - 1] + "…"


# ====================================================================== 둥근 도형
def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class RoundBox(tk.Canvas):
    """둥근 사각형 면. 내용은 .inner(Frame)에 넣는다. fit=True면 높이를 내용에 맞춘다."""

    def __init__(self, parent, *, radius=16, fill=BG_SURFACE, outline=None, width_px=1, padx=16, pady=16,
                 fit=False, bg=None, **kw):
        super().__init__(parent, highlightthickness=0, bd=0, bg=bg or bg_of(parent), **kw)
        self.radius, self.fill, self.outline, self.width_px = radius, fill, outline, width_px
        self.padx, self.pady, self.fit = padx, pady, fit
        self.inner = tk.Frame(self, bg=fill)
        self._win = self.create_window(padx, pady, window=self.inner, anchor="nw")
        self.bind("<Configure>", self._redraw)
        if fit:
            self.inner.bind("<Configure>", self._fit)

    def _fit(self, _e=None) -> None:
        want = self.inner.winfo_reqheight() + 2 * self.pady
        if int(self.cget("height")) != want:
            self.configure(height=want)

    def _redraw(self, _e=None) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("shape")
        o = self.width_px / 2
        rounded_rect(self, o, o, w - o - 1, h - o - 1, self.radius, fill=self.fill,
                     outline=self.outline or self.fill, width=self.width_px if self.outline else 1, tags="shape")
        self.tag_lower("shape")
        self.itemconfigure(self._win, width=max(1, w - 2 * self.padx))
        if not self.fit:
            self.itemconfigure(self._win, height=max(1, h - 2 * self.pady))

    def restyle(self, fill=None, outline=None, width_px=None) -> None:
        if fill is not None:
            self.fill = fill
            self.inner.configure(bg=fill)
        if outline is not None:
            self.outline = outline or None
        if width_px is not None:
            self.width_px = width_px
        self._redraw()


class Card(RoundBox):
    """카드: bg/surface, border/default, 큰 모서리. 제목 줄(제목·설명 왼쪽, .actions 오른쪽) + .body"""

    def __init__(self, parent, title: str = "", caption: str = "", pad: int = 24, fit=False, **kw):
        super().__init__(parent, radius=px(28), fill=BG_SURFACE, outline=BORDER_DEFAULT,
                         padx=px(pad), pady=px(pad - 4), fit=fit, **kw)
        head = tk.Frame(self.inner, bg=BG_SURFACE)
        head.pack(fill="x")
        text = tk.Frame(head, bg=BG_SURFACE)
        text.pack(side="left", fill="x", expand=True)
        self.title = tk.Label(text, text=title, font=F["heading20"], bg=BG_SURFACE, fg=TEXT_PRIMARY, anchor="w")
        if title:
            self.title.pack(fill="x")
        self.caption = tk.Label(text, text=caption, font=F["label14r"], bg=BG_SURFACE, fg=TEXT_SECONDARY,
                                anchor="w", justify="left")
        if caption:
            self.caption.pack(fill="x", pady=(px(2), 0))
        self.actions = tk.Frame(head, bg=BG_SURFACE)
        self.actions.pack(side="right")
        self.body = tk.Frame(self.inner, bg=BG_SURFACE)
        self.body.pack(fill="both", expand=True, pady=(px(12) if (title or caption) else 0, 0))


class Banner(RoundBox):
    """상태 안내 (Inline hint): 톤별 면, 제목·내용 왼쪽, 버튼 오른쪽(세로 가운데)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, radius=px(16), fill=TONES["neutral"][0], padx=px(20), pady=px(14), fit=True, **kw)
        row = tk.Frame(self.inner, bg=self.fill)
        row.pack(fill="x")
        self._row = row
        text = tk.Frame(row, bg=self.fill)
        text.pack(side="left", fill="x", expand=True)
        self._text = text
        self.title = tk.Label(text, font=F["heading20"], anchor="w", justify="left", bg=self.fill, fg=TEXT_PRIMARY)
        self.title.pack(fill="x")
        self.detail = tk.Label(text, font=F["label14r"], anchor="w", justify="left", bg=self.fill, fg=TEXT_PRIMARY)
        self.detail.pack(fill="x", pady=(px(4), 0))
        self.actions = tk.Frame(row, bg=self.fill)
        self.actions.pack(side="right", padx=(px(16), 0))
        self.bind("<Configure>", self._wrap, add="+")

    def _wrap(self, _e=None) -> None:
        width = max(px(200), self.winfo_width() - 2 * self.padx - self.actions.winfo_reqwidth() - px(24))
        self.title.configure(wraplength=width)
        self.detail.configure(wraplength=width)

    def set(self, tone: str, title: str, detail: str = "") -> None:
        fill, fg = TONES[tone]
        self.restyle(fill=fill, outline=BORDER_DEFAULT if tone == "neutral" else "")
        for w in (self._row, self._text, self.actions, self.title, self.detail):
            w.configure(bg=fill)
        for child in self.actions.winfo_children():
            if isinstance(child, Button):
                child.set_bg(fill)
        self.title.configure(text=title, fg=fg)
        self.detail.configure(text=detail, fg=TEXT_PRIMARY)
        if detail:
            self.detail.pack(fill="x", pady=(px(4), 0))
        else:
            self.detail.pack_forget()


# ====================================================================== 버튼·배지
_KINDS = {
    # 면, 글자, 테두리, hover 면, hover 글자, 비활성 면, 비활성 글자, 비활성 테두리
    "primary": (BG_PRIMARY, TEXT_ON_PRIMARY, None, BG_PRIMARY_PRESSED, TEXT_ON_PRIMARY, BG_SUBTLE, TEXT_DISABLED, None),
    "secondary": (BG_SUBTLE, TEXT_PRIMARY, BORDER_STRONG, BG_NEUTRAL, TEXT_PRIMARY, BG_SURFACE, TEXT_DISABLED,
                  BORDER_DISABLED),
    "ghost": (None, TEXT_SECONDARY, None, BG_SUBTLE, TEXT_PRIMARY, None, TEXT_DISABLED, None),
    "danger": (None, TEXT_DANGER, None, BG_SUBTLE, TEXT_DANGER, None, TEXT_DISABLED, None),
}


class Button(tk.Canvas):
    """알약 모양 버튼 (primary·secondary·ghost). configure/cget의 text·state·command를 tk.Button처럼 받는다."""

    def __init__(self, parent, text: str, command=None, kind: str = "secondary", size: str = "md", width=None):
        self._h = px(48 if size == "md" else 40)
        self._padx = px(20 if size == "md" else 16)
        self._font = F["body16"] if size == "md" else F["label14"]
        self._fixed = width
        super().__init__(parent, height=self._h, highlightthickness=0, bd=0, bg=bg_of(parent), cursor="hand2")
        self._text, self._command, self._kind, self._state = text, command, kind, "normal"
        self._hover = self._pressed = False
        self._resize()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)

    def _resize(self) -> None:
        width = self._fixed or tkfont.Font(font=self._font).measure(self._text) + 2 * self._padx
        super().configure(width=width)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        mapped = self.winfo_ismapped() and self.winfo_width() > 1
        w = self.winfo_width() if mapped else int(self.cget("width"))  # 늘어나거나 줄어든 실제 폭
        h = self._h
        fill, fg, border, hfill, hfg, dfill, dfg, dborder = _KINDS[self._kind]
        if self._state == "disabled":
            fill, fg, border = dfill, dfg, dborder
        elif self._hover or self._pressed:
            fill, fg = hfill, hfg
        if fill or border:
            rounded_rect(self, 0.5, 0.5, w - 1.5, h - 1.5, h / 2, fill=fill or bg_of(self),
                         outline=border or fill or bg_of(self), width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        if self._state == "normal":
            self._draw()

    def _press(self, _e) -> None:
        if self._state == "normal":
            self._pressed = True
            self._draw()

    def _release(self, e) -> None:
        inside = 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()
        fire = self._pressed and inside and self._state == "normal"
        self._pressed = False
        self._draw()
        if fire and self._command:
            self._command()

    def set_bg(self, color: str) -> None:
        super().configure(bg=color)
        self._draw()

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._draw()

    def invoke(self) -> None:
        if self._state == "normal" and self._command:
            self._command()

    def configure(self, cnf=None, **kw):
        kw = {**(cnf or {}), **kw}
        changed = False
        if "text" in kw:
            self._text = kw.pop("text")
            changed = True
        if "state" in kw:
            self._state = kw.pop("state")
            super().configure(cursor="hand2" if self._state == "normal" else "arrow")
            changed = True
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            super().configure(**kw)
        if changed:
            self._resize()

    config = configure

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        return super().cget(key)


class Badge(tk.Canvas):
    """배지: 높이 22, 톤별 면과 글자. 문구와 색을 함께 쓴다."""

    TONES = {"neutral": (BG_NEUTRAL, TEXT_PRIMARY), "warning": (subtle("warning"), TEXT_WARNING),
             "danger": (subtle("danger"), TEXT_DANGER), "success": (subtle("success"), TEXT_SUCCESS)}

    def __init__(self, parent, text: str = "", tone: str = "neutral"):
        super().__init__(parent, height=px(22), highlightthickness=0, bd=0, bg=bg_of(parent))
        self._text, self._tone = text, tone
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if not self._text:
            super().configure(width=1)
            return
        fill, fg = self.TONES[self._tone]
        w = tkfont.Font(font=F["badge11"]).measure(self._text) + px(16)
        h = px(22)
        super().configure(width=w)
        rounded_rect(self, 0, 0, w - 1, h - 1, h / 2, fill=fill, outline=fill)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=F["badge11"])

    def set(self, text: str, tone: str = "neutral") -> None:
        self._text, self._tone = text, tone
        self._draw()

    def cget(self, key):
        return self._text if key == "text" else super().cget(key)


# ====================================================================== 입력
class _Focusable(RoundBox):
    """조작 요소 테두리: 기본 border/strong 1px, 포커스 border/focus 2px."""

    def watch(self, widget: tk.Misc) -> None:
        widget.bind("<FocusIn>", lambda e: self.restyle(outline=BORDER_FOCUS, width_px=2), add="+")
        widget.bind("<FocusOut>", lambda e: self.restyle(outline=BORDER_STRONG, width_px=1), add="+")


def scrolled_text(parent, **kw) -> tk.Text:
    """글 상자 (Textarea): 둥근 테두리 + 어두운 스크롤바. Text를 돌려준다."""
    box = _Focusable(parent, radius=px(16), fill=BG_SURFACE, outline=BORDER_STRONG, padx=px(12), pady=px(10))
    box.pack(fill="both", expand=True)
    text = tk.Text(box.inner, wrap="word", font=F["body16r"], relief="flat", bd=0, highlightthickness=0,
                   bg=BG_SURFACE, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY, selectbackground=BORDER_STRONG,
                   selectforeground=TEXT_PRIMARY, padx=px(8), pady=px(6), spacing1=px(2), spacing3=px(2), **kw)
    bar = ttk.Scrollbar(box.inner, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    box.watch(text)
    return text


class Input(_Focusable):
    """한 줄 입력 (Input): 알약 모양, 높이 48."""

    def __init__(self, parent, textvariable=None, **kw):
        super().__init__(parent, radius=px(24), fill=BG_SURFACE, outline=BORDER_STRONG, padx=px(20), pady=px(10),
                         height=px(48), **kw)
        self.entry = tk.Entry(self.inner, textvariable=textvariable, font=F["body16r"], relief="flat", bd=0,
                              highlightthickness=0, bg=BG_SURFACE, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                              selectbackground=BORDER_STRONG, selectforeground=TEXT_PRIMARY)
        self.entry.pack(fill="both", expand=True)
        self.watch(self.entry)


class Select(Button):
    """선택 상자 (Select): 보조 버튼 모양 + 오른쪽 화살표, 누르면 어두운 메뉴로 고른다. Combobox처럼 get/current."""

    def __init__(self, parent, values, size: str = "md"):
        self._values = list(values)
        self._value = self._values[0] if self._values else ""
        super().__init__(parent, self._value, self._open, kind="secondary", size=size)

    def _resize(self) -> None:
        f = tkfont.Font(font=self._font)
        width = max((f.measure(v) for v in self._values), default=0) + self._padx + px(44)
        tk.Canvas.configure(self, width=width)
        self._draw()

    def _draw(self) -> None:
        super()._draw()
        self.delete("label")
        mapped = self.winfo_ismapped() and self.winfo_width() > 1
        w, h = (self.winfo_width() if mapped else int(self.cget("width"))), self._h
        for item in self.find_all():  # 가운데 글자 대신 왼쪽 정렬
            if self.type(item) == "text":
                self.delete(item)
        fg = TEXT_PRIMARY if self._state == "normal" else TEXT_DISABLED
        self.create_text(self._padx, h / 2, text=self._text, fill=fg, font=self._font, anchor="w")
        x, y, s = w - px(28), h / 2, px(4)
        self.create_line(x - s, y - s / 2, x, y + s / 2, x + s, y - s / 2, fill=GRAY[300], width=1.4,
                         capstyle="round", joinstyle="round")

    def _open(self) -> None:
        m = menu(self)
        var = tk.StringVar(self, self._value)
        for v in self._values:
            m.add_radiobutton(label=v, value=v, variable=var, command=lambda v=v: self.set(v))
        try:
            m.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() + px(4))
        finally:
            m.grab_release()

    def set(self, value: str) -> None:
        self._value = value
        self.configure(text=value)

    def get(self) -> str:
        return self._value

    def current(self, i: int) -> None:
        self.set(self._values[i])


class Switch(tk.Frame):
    """켜기·끄기 (Switch): 44×24. 끔 = 어두운 트랙, 켬 = 밝은 트랙. 상태색·앰버를 쓰지 않는다."""

    def __init__(self, parent, text: str, variable: tk.BooleanVar, command=None):
        super().__init__(parent, bg=bg_of(parent))
        self.var, self.command = variable, command
        self.canvas = tk.Canvas(self, width=px(44), height=px(24), highlightthickness=0, bd=0, bg=bg_of(parent),
                                cursor="hand2")
        self.canvas.pack(side="left")
        self.label = tk.Label(self, text=text, font=F["body16r"], bg=bg_of(parent), fg=TEXT_PRIMARY, cursor="hand2")
        self.label.pack(side="left", padx=(px(12), 0))
        for w in (self.canvas, self.label):
            w.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _toggle(self, _e=None) -> None:
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h, on = px(44), px(24), self.var.get()
        track = GRAY[200] if on else SWITCH_OFF_TRACK
        rounded_rect(c, 0.5, 0.5, w - 1.5, h - 1.5, h / 2, fill=track, outline=track if on else BORDER_STRONG)
        d = h - px(4) * 2
        x = w - px(4) - d if on else px(4)
        c.create_oval(x, px(4), x + d, px(4) + d, fill=GRAY[950] if on else GRAY[400], outline="")


class Segmented(RoundBox):
    """적은 수의 단일 선택 (Segmented): bg/subtle 바탕, 고른 칸은 bg/surface + border/focus 2px."""

    def __init__(self, parent, options, variable: tk.StringVar, command=None):
        super().__init__(parent, radius=px(24), fill=BG_SUBTLE, outline=BORDER_STRONG, padx=px(4), pady=px(4),
                         fit=True)
        self.var, self.command, self.items = variable, command, []
        row = tk.Frame(self.inner, bg=BG_SUBTLE)
        row.pack(fill="x")
        for value, label in options:
            c = tk.Canvas(row, width=px(60), height=px(40), highlightthickness=0, bd=0, bg=BG_SUBTLE, cursor="hand2")
            c.pack(side="left", fill="x", expand=True, padx=(0, px(4)))
            c.bind("<Configure>", lambda e: self._draw())
            c.bind("<Button-1>", lambda e, v=value: self._choose(v))
            self.items.append((value, label, c))
        variable.trace_add("write", lambda *a: self._draw())

    def _choose(self, value: str) -> None:
        if self.var.get() != value:
            self.var.set(value)
            if self.command:
                self.command()

    def _draw(self) -> None:
        for value, label, c in self.items:
            c.delete("all")
            w, h = c.winfo_width(), px(40)
            if w < 4:
                continue
            chosen = self.var.get() == value
            if chosen:
                rounded_rect(c, 1, 1, w - 2, h - 2, h / 2, fill=BG_SURFACE, outline=BORDER_FOCUS, width=2)
            c.create_text(w / 2, h / 2, text=label, font=F["label14"],
                          fill=TEXT_PRIMARY if chosen else TEXT_SECONDARY)


# ====================================================================== 떠 있는 것: 메뉴·툴팁·토스트·대화상자
def menu(parent) -> tk.Menu:
    return tk.Menu(parent, tearoff=0, bg=BG_RAISED, fg=TEXT_PRIMARY, activebackground=BG_SELECTED,
                   activeforeground=TEXT_PRIMARY, disabledforeground=TEXT_TERTIARY, selectcolor=AMBER_500,
                   font=F["label14"], relief="solid", bd=1, activeborderwidth=0)


_KEY = "#010203"  # 창 모서리를 투명하게 할 색 (Windows)


def _floating(master, width: int, height: int, radius: int, fill: str, outline: str | None):
    """둥근 모서리의 떠 있는 창. Windows에서는 모서리를 투명하게, 다른 OS에서는 화면 배경색으로."""
    win = tk.Toplevel(master)
    win.wm_overrideredirect(True)
    win.attributes("-topmost", True)
    key = BG_CANVAS
    if sys.platform == "win32":
        try:
            win.attributes("-transparentcolor", _KEY)
            key = _KEY
        except tk.TclError:
            pass
    win.configure(bg=key)
    c = tk.Canvas(win, width=width, height=height, highlightthickness=0, bd=0, bg=key)
    c.pack()
    rounded_rect(c, 0.5, 0.5, width - 1.5, height - 1.5, radius, fill=fill, outline=outline or fill)
    return win, c


class Tooltip:
    def __init__(self, master):
        self.master = master
        self.win = None

    def show(self, x: int, y: int, text: str) -> None:
        self.hide()
        f = tkfont.Font(font=F["label14"])
        lines = text.split("\n")
        w = min(px(420), max(f.measure(line) for line in lines) + px(32))
        h = f.metrics("linespace") * len(lines) + px(24)
        self.win, c = _floating(self.master, w, h, px(16), BG_RAISED, BORDER_STRONG)
        c.create_text(px(16), px(12), text=text, anchor="nw", fill=TEXT_PRIMARY, font=F["label14"], width=w - px(32))
        self.win.geometry(f"+{x + px(14)}+{y + px(18)}")

    def hide(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None


def toast(master, message: str, ms: int = 3500):
    """토스트: 높이 56 알약, 앰버 면, 화면 아래 가운데."""
    f = tkfont.Font(font=F["body16"])
    limit = min(px(720), master.winfo_screenwidth() - px(80))
    one_line = f.measure(message) + px(72)
    w = min(one_line, limit)
    lines = 1 if one_line <= limit else 1 + f.measure(message) // (limit - px(72))
    h = max(px(56), lines * f.metrics("linespace") + px(32))  # 한 줄이면 높이 56
    win, c = _floating(master, w, h, min(h / 2, px(28)), BG_PRIMARY, None)
    c.create_text(w / 2, h / 2, text=message, fill=TEXT_ON_PRIMARY, font=F["body16"], justify="center",
                  width=w - px(48) if lines > 1 else 0)
    x = (master.winfo_screenwidth() - w) // 2
    y = master.winfo_screenheight() - h - px(72)
    win.geometry(f"+{x}+{y}")
    return win


class Dialog(tk.Toplevel):
    """대화상자: 폭 520, bg/raised, border/strong, 제목 title/24. .body와 .buttons(오른쪽 정렬)."""

    def __init__(self, master, title: str):
        super().__init__(master)
        self.title(title)
        self.configure(bg=BORDER_STRONG, padx=1, pady=1)  # 1px 테두리
        self.resizable(False, False)
        self.transient(master)
        self.minsize(px(520), 1)
        frame = tk.Frame(self, bg=BG_RAISED, padx=px(24), pady=px(24))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=title, font=F["title24"], bg=BG_RAISED, fg=TEXT_PRIMARY, anchor="w").pack(fill="x")
        self.body = tk.Frame(frame, bg=BG_RAISED, width=px(472))
        self.body.pack(fill="both", expand=True, pady=(px(12), px(24)))
        self.buttons = tk.Frame(frame, bg=BG_RAISED)
        self.buttons.pack(fill="x")


# ====================================================================== 표
def table(parent, columns, height: int = 6) -> ttk.Treeview:
    """정보 표: 머리 label/14 text/secondary, 행 body/16. 오른쪽에 어두운 스크롤바."""
    frame = tk.Frame(parent, bg=bg_of(parent))
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height, selectmode="none")
    for cid, label, width in columns:
        tree.heading(cid, text=label, anchor="w")
        tree.column(cid, width=px(width), anchor="w", stretch=cid == columns[-1][0])
    bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    tree.tag_configure("muted", foreground=TEXT_SECONDARY)
    tree.tag_configure("warning", foreground=TEXT_WARNING)
    tree.tag_configure("danger", foreground=TEXT_DANGER)
    return tree


def label(parent, text: str = "", style: str = "label14r", fg: str = TEXT_SECONDARY, **kw) -> tk.Label:
    return tk.Label(parent, text=text, font=F[style], bg=bg_of(parent), fg=fg, anchor="w", justify="left", **kw)
