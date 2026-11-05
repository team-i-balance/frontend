"""AI 누리 보안 디자인 설정(docs/DESIGN-SETTINGS.md)을 tkinter로 옮긴 것.

- 다크 하나. 구조는 면으로 나눈다: 바탕 #1E2126 → 패널 #25292D(테두리 없음) → 요소 #343A40(입력·문장 상자·버튼 면).
  선(#3A4148)은 표 행 사이·구역 사이에만 쓴다.
- 시그널 블루는 세 곳에만: 고른 것(옅은 파란 면), 창마다 주 행동 하나(#3570E0 채운 버튼), 키보드 포커스(#8FB5FF).
- 글자색은 사실상 두 단계: 값·문장은 #F1F3F5, 메타·라벨은 #A3ABB4. 비활성 #868E96은 꺼진 것·입력 안내에만.
- 진한 상태색(빨강·초록·노랑)은 아이콘·숫자·원문 표시에만. 넓은 면은 같은 색 12%를 바탕에 미리 섞은 옅은 면.
- 글꼴은 Pretendard 하나(앱에 동봉, agent/assets/fonts, OFL). 크기 12·13·14·15·16·18·20·22·28, 굵기 400·500·600만.
- tkinter가 못 하는 것: 자간, tabular-nums, 그림자, 알파. 옅은 면은 놓일 바탕에 미리 섞고, 떠 있는 것(칩·툴팁·알림·
  대화상자)은 그림자 대신 #3A4148 1px 선으로 바탕과 나눈다. 둥근 모서리는 Canvas로 그린다. 크기는 화면 배율(DPI)에 맞춘다.
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
APP_TITLE = "AI 누리 보안"


# ====================================================================== 색
def mix(color: str, alpha: float, base: str) -> str:
    """color를 alpha만큼 base 위에 섞은 불투명 색 (CSS의 rgba·color-mix를 바탕에 미리 섞은 값)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(alpha * x + (1 - alpha) * y):02X}" for x, y in zip(a, b))


def blend(rgb: tuple[int, int, int], alpha: float, base: str) -> str:
    """예전 이름: rgba(r,g,b,alpha)를 base 위에 섞은 불투명 색."""
    return mix("#" + "".join(f"{c:02X}" for c in rgb), alpha, base)


# 면
BG = "#1E2126"          # 바탕: 창 전체
SIDE_BG = "#1A1D21"     # 사이드바(바탕보다 한 단계 어둡게)
SURFACE = "#25292D"     # 패널·카드, 떠 있는 것(칩·툴팁·대화상자)
SURFACE_2 = "#2B3035"   # 펼친 행, 하단 판단 막대
WELL = "#343A40"        # 입력칸, 문장 상자, 버튼 면, 표 머리
CHIP = "#3B4148"        # 라벨 바탕, 버튼에 마우스 올림, 변환된 값
HOVER = "#2E3439"       # 표·목록 행에 마우스 올림
PRESS = "#42484F"       # 누르는 순간
# 글자
FG = "#F1F3F5"          # 이름·값·본문 문장
SUB = "#C1C7CE"         # 둘째 줄 설명, 보조 버튼 글자 (표 셀에는 쓰지 않음)
MUTE = "#A3ABB4"        # 시각·메타·라벨·표 열 머리·부제
FAINT = "#868E96"       # 꺼진 항목·입력 안내 문구에만 (정보 글자에는 쓰지 않음)
# 선
LINE = "#3A4148"        # 표 행·구역 구분, 떠 있는 것의 테두리(그림자 대신)
LINE_2 = "#59616B"      # 버튼·입력 테두리
# 강조 (시그널 블루)
ACC = "#3D7EF7"         # 표시: 선택 막대, 로고, 체크. 글자에는 쓰지 않음
ACC_FILL = "#3570E0"    # 채운 버튼 바탕에만 (흰 글자 4.6:1)
ACC_ON = "#FFFFFF"
ACC_INK = "#8FB5FF"     # 어두운 면 위 파란 글자·링크, 포커스 테두리
FOCUS = ACC_INK
ACC_TILE = mix(ACC, .16, SURFACE)              # 선택 면 (≈ #2A3650)
ACC_FILL_HOVER = mix("#000000", .12, ACC_FILL)  # color-mix(acc-fill 88%, #000)
ACC_FILL_PRESS = mix("#000000", .24, ACC_FILL)
# 상태
DANGER = "#FF7272"      # 원문 전송, 위반, 오류
OK = "#55DC92"          # 완료, 적용
WARN = "#F8C952"        # 기밀, 회피 의심
INFO = "#62CAF4"        # 안내 알림
DANGER_FILL = "#D96870"  # 넓은 면(차트 막대)
OK_FILL = "#4BAA7B"     # 넓은 면(진행 막대)
BAR = "#5C646D"         # 차트·가로 막대 기본, 마우스를 올리면 SUB
SWITCH_ON, SWITCH_THUMB_ON = "#365989", "#DCE8FA"
TONE_COLOR = {"danger": DANGER, "error": DANGER, "success": OK, "warning": WARN, "info": INFO,
              "todo": ACC_INK, "neutral": SUB}


def soft(tone: str, on: str = SURFACE) -> str:
    """옅은 면: 상태색 12%를 on 바탕 위에 섞은 값."""
    return mix(TONE_COLOR[tone], .12, on)


# ---------------------------------------------------------------- 예전 이름 (다른 창이 쓰는 이름을 새 값으로)
BG_CANVAS = BG
BG_SURFACE = SURFACE
BG_RAISED = SURFACE         # 떠 있는 것도 패널색. 그림자 대신 LINE 테두리
BG_SUBTLE = WELL
BG_SELECTED = ACC_TILE
BG_NEUTRAL = CHIP
BG_PRIMARY = ACC_FILL
BG_PRIMARY_PRESSED = ACC_FILL_PRESS
TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY, TEXT_DISABLED = FG, MUTE, MUTE, FAINT
TEXT_ON_PRIMARY, TEXT_ACCENT = ACC_ON, ACC_INK
TEXT_DANGER, TEXT_SUCCESS, TEXT_WARNING = DANGER, OK, WARN
BORDER_DEFAULT, BORDER_STRONG, BORDER_FOCUS = LINE, LINE_2, FOCUS
BORDER_DANGER, BORDER_DISABLED = DANGER, LINE


def subtle(tone: str, on: str = SURFACE) -> str:
    return soft(tone, on)


# 상태 톤: (면, 글자)
TONES = {
    "danger": (soft("danger", BG), DANGER),
    "success": (soft("success", BG), OK),
    "warning": (soft("warning", BG), WARN),
    "neutral": (SURFACE, FG),
}
GRADE_TONE = {"O": "success", "S": "warning", "C": "danger"}

# 문장 상자(WELL) 안의 값 표시 (Text 태그). 디자인 설정 '가린 값'
HIT = {"background": mix(ACC, .22, WELL), "foreground": FG}                 # 잡힌 값 (가릴 값)
CONVERTED = {"background": CHIP, "foreground": SUB}                          # 변환된 값
RAW = {"background": soft("danger", WELL), "foreground": DANGER}             # 원문 그대로 나가는 값
SUSPECT = {"background": soft("warning", WELL), "foreground": WARN, "underline": True}  # 확인 필요
BLOCKED = {"background": soft("warning", WELL), "foreground": WARN}          # 기밀 (외부 전송 차단)
RESTORED = {"background": soft("success", WELL), "foreground": FG}           # 원래 값으로 되돌린 곳
DELETED = {"foreground": MUTE, "overstrike": True}                           # 지운 값
MARK, KEPT, SAFE = HIT, RAW, CONVERTED                                       # 예전 이름

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

    def regular(size):  # 400
        return (FAMILY, -px(size))

    def med(size):      # 500 (따로 없으면 400)
        return (medium, -px(size)) if medium else (FAMILY, -px(size))

    def semi(size):     # 600 (따로 없으면 굵게)
        return (semibold, -px(size)) if semibold else (FAMILY, -px(size), "bold")

    F.clear()
    for size in (12, 13, 14, 15, 16):
        F[f"t{size}"], F[f"t{size}m"], F[f"t{size}s"] = regular(size), med(size), semi(size)
    for size in (18, 20, 22, 28):
        F[f"t{size}s"] = semi(size)
    F["code13"] = (code, -px(13))
    # 예전 이름 → 새 단계 (12보다 작게, 600보다 굵게 쓰지 않는다)
    F.update({"title32": F["t28s"], "title24": F["t22s"], "heading20": F["t20s"], "body16": F["t16m"],
              "body16r": F["t16"], "label14": F["t14m"], "label14r": F["t13"], "caption12": F["t12"],
              "badge11": F["t12m"], "code12": F["code13"], "mark16": F["t16m"]})


# 예전 이름 (다른 창이 아직 쓰는 경우)
FONT = SMALL = BOLD = TITLE = TEXT = ("TkDefaultFont", 10)
PRIMARY, MUTED = BG_PRIMARY, TEXT_SECONDARY


def setup(root: tk.Tk) -> None:
    """창을 만들기 전에 한 번. 배율·글꼴·ttk 스타일·기본 색을 정한다."""
    global SCALE, FONT, SMALL, BOLD, TITLE, TEXT
    try:
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        SCALE = 1.0
    _fonts(root)
    FONT, SMALL, BOLD, TITLE, TEXT = F["t14"], F["t13"], F["t14m"], F["t22s"], F["t16"]
    root.configure(bg=BG)
    root.option_add("*Font", F["t14"])
    root.option_add("*Background", BG)
    root.option_add("*Foreground", FG)
    root.option_add("*selectBackground", ACC_TILE)
    root.option_add("*selectForeground", FG)
    try:
        app_icon(root)
    except tk.TclError:
        pass

    style = ttk.Style(root)
    style.theme_use("clam")  # 기본 Windows 테마는 색을 바꾸지 못한다
    style.configure(".", background=BG, foreground=FG, font=F["t14"], bordercolor=LINE, troughcolor=SURFACE,
                    focuscolor=FOCUS)
    # 표: 머리 바탕 없음 12/500 회색, 행 40, 세로선·바깥 테두리 없음. 고른 행은 선택 면
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=FG,
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                    rowheight=px(40), font=F["t14"], borderwidth=0)
    style.map("Treeview", background=[("selected", ACC_TILE)], foreground=[("selected", FG)])
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTE, font=F["t12m"],
                    relief="flat", borderwidth=0, bordercolor=LINE, lightcolor=SURFACE,
                    darkcolor=SURFACE, padding=(px(12), px(10)))
    style.map("Treeview.Heading", background=[("active", SURFACE)], foreground=[("active", FG)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # 바깥 테두리 없음
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", troughcolor=SURFACE, background=CHIP,
                        bordercolor=SURFACE, lightcolor=CHIP, darkcolor=CHIP,
                        arrowcolor=MUTE, gripcount=0, arrowsize=px(12), relief="flat")
        style.map(f"{orient}.TScrollbar", background=[("active", LINE_2)])
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=px(16), gripcount=0, background=BG, lightcolor=BG, bordercolor=BG)
    # 대화상자의 세로 선택지 (라디오): 고르면 시그널 블루로 채운 원
    style.configure("Dialog.TRadiobutton", background=SURFACE, foreground=FG, font=F["t14"],
                    indicatorbackground=WELL, indicatorforeground=ACC_ON, indicatormargin=(0, 0, px(12), 0),
                    upperbordercolor=LINE_2, lowerbordercolor=LINE_2, focuscolor=SURFACE, padding=(0, px(6)))
    style.map("Dialog.TRadiobutton", background=[("active", SURFACE)],
              indicatorbackground=[("selected", ACC)], indicatorforeground=[("selected", ACC_ON)])


def window(win: tk.Toplevel, title: str, width: int, height: int, minw: int | None = None,
           minh: int | None = None) -> None:
    """창 공통: 바탕, 제목, 크기(배율 반영, 화면보다 크면 줄임)."""
    win.title(title)
    win.configure(bg=BG)
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
        return BG


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


def _measure(font, text: str) -> int:
    return tkfont.Font(font=font).measure(text)


# ====================================================================== 둥근 도형·아이콘·로고
def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


# 로고 마크 '문장과 가림': 64칸 그리드 위의 면 (docs/DESIGN-SETTINGS.md 7장)
MARK_RECTS = ((8, 12, 56, 20), (8, 28, 20, 36), (8, 44, 44, 52), (24, 24, 56, 40))


def draw_mark(canvas: tk.Canvas, x: float, y: float, size: float, color: str = ACC, tags=()) -> None:
    s = size / 64
    for x1, y1, x2, y2 in MARK_RECTS:
        canvas.create_rectangle(x + x1 * s, y + y1 * s, x + x2 * s, y + y2 * s, fill=color, outline="", tags=tags)


def mark_photo(master: tk.Misc, size: int, color: str = ACC, background: str | None = BG) -> tk.PhotoImage:
    """마크를 그린 PhotoImage. background가 있으면 파비콘처럼 둥근 네모(모서리 14/64) 위에 둔다."""
    img = tk.PhotoImage(master=master, width=size, height=size)
    s = size / 64
    if background:
        img.put(background, to=(0, 0, size, size))
        r = 14 * s
        for yy in range(size):
            for xx in range(size):
                cx = min(max(xx + .5, r), size - r)
                cy = min(max(yy + .5, r), size - r)
                if (xx + .5 - cx) ** 2 + (yy + .5 - cy) ** 2 > r * r:
                    img.transparency_set(xx, yy, True)
    for x1, y1, x2, y2 in MARK_RECTS:
        img.put(color, to=(round(x1 * s), round(y1 * s), max(round(x1 * s) + 1, round(x2 * s)),
                           max(round(y1 * s) + 1, round(y2 * s))))
    return img


def app_icon(root: tk.Tk) -> None:
    """작업 표시줄·창 아이콘: 파비콘과 같은 그림."""
    icons = [mark_photo(root, n) for n in (16, 32, 64)]
    root.iconphoto(True, *icons)
    root._app_icons = icons  # 참조를 남겨 둔다 (지우면 아이콘이 사라진다)


# 가는 선 아이콘(lucide 계열, 24칸 그리드, 선 굵기 1.5). 16(글자 옆)·20(메뉴) 두 크기만 쓴다
def draw_icon(canvas: tk.Canvas, name: str, x: float, y: float, size: int = 16, color: str = SUB, tags=()) -> None:
    s = size / 24
    w = max(1.0, 1.5 * size / 16)
    kw = dict(fill=color, width=w, capstyle="round", joinstyle="round", tags=tags)

    def p(*pts):
        return [v * s + (x if i % 2 == 0 else y) for i, v in enumerate(pts)]

    def circle(cx=12, cy=12, r=10):
        canvas.create_oval(*p(cx - r, cy - r, cx + r, cy + r), outline=color, width=w, tags=tags)

    def dot(cx, cy):
        r = w * .7
        canvas.create_oval(x + cx * s - r, y + cy * s - r, x + cx * s + r, y + cy * s + r, fill=color,
                           outline="", tags=tags)

    if name == "success":          # circle-check
        circle()
        canvas.create_line(*p(8.5, 12.2, 11, 14.6, 15.8, 9.6), **kw)
    elif name == "warning":        # triangle-alert
        canvas.create_line(*p(12, 3.5, 21.5, 19.8, 2.5, 19.8, 12, 3.5), **kw)
        canvas.create_line(*p(12, 9.5, 12, 13.5), **kw)
        dot(12, 16.8)
    elif name in ("danger", "error"):  # circle-x
        circle()
        canvas.create_line(*p(15, 9, 9, 15), **kw)
        canvas.create_line(*p(9, 9, 15, 15), **kw)
    elif name in ("info", "neutral"):  # info
        circle()
        canvas.create_line(*p(12, 16, 12, 11.5), **kw)
        dot(12, 8)
    elif name == "todo":           # circle-arrow-right
        circle()
        canvas.create_line(*p(8, 12, 16, 12), **kw)
        canvas.create_line(*p(12.5, 8.5, 16, 12, 12.5, 15.5), **kw)
    elif name == "check":
        canvas.create_line(*p(20, 6, 9, 17, 4, 12), **kw)
    elif name == "back":           # chevron-left
        canvas.create_line(*p(15, 18, 9, 12, 15, 6), **kw)
    elif name == "chevron-down":
        canvas.create_line(*p(6, 9, 12, 15, 18, 9), **kw)
    elif name == "close":          # x
        canvas.create_line(*p(18, 6, 6, 18), **kw)
        canvas.create_line(*p(6, 6, 18, 18), **kw)
    elif name == "undo":           # undo-2
        canvas.create_line(*p(9, 14, 4, 9, 9, 4), **kw)
        canvas.create_line(*p(4, 9, 14.5, 9), **kw)
        canvas.create_arc(*p(9, 9, 20, 20), start=270, extent=180, style="arc", outline=color, width=w, tags=tags)
        canvas.create_line(*p(14.5, 20, 11, 20), **kw)


class Icon(tk.Canvas):
    def __init__(self, parent, name: str, color: str = SUB, size: int = 16):
        n = px(size)
        super().__init__(parent, width=n, height=n, highlightthickness=0, bd=0, bg=bg_of(parent))
        self.name, self.size = name, n
        self.set(name, color)

    def set(self, name: str, color: str) -> None:
        self.delete("all")
        self.name = name
        draw_icon(self, name, 0, 0, self.size, color)


# ====================================================================== 면
class RoundBox(tk.Canvas):
    """둥근 사각형 면. 내용은 .inner(Frame)에 넣는다. fit=True면 높이를 내용에 맞춘다."""

    def __init__(self, parent, *, radius=16, fill=SURFACE, outline=None, width_px=1, padx=16, pady=16,
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
    """패널: #25292D, 테두리 없음, 모서리 16, 여백 20/24. 제목 16/600, 제목 옆 부제 13 회색, 오른쪽 .actions, 아래 .body"""

    def __init__(self, parent, title: str = "", caption: str = "", pad: int = 24, fit=False, **kw):
        super().__init__(parent, radius=px(16), fill=SURFACE, outline=None,
                         padx=px(pad), pady=px(max(12, pad - 4)), fit=fit, **kw)
        head = tk.Frame(self.inner, bg=SURFACE)
        head.pack(fill="x")
        text = tk.Frame(head, bg=SURFACE)
        text.pack(side="left", fill="x", expand=True)
        line = tk.Frame(text, bg=SURFACE)
        line.pack(fill="x")
        self.title = tk.Label(line, text=title, font=F["t16s"], bg=SURFACE, fg=FG, anchor="w")
        if title:
            self.title.pack(side="left")
        # 짧은 부제는 제목 옆에, 긴 설명은 제목 아래에 둔다
        beside = len(caption) <= 16
        self.caption = tk.Label(line if beside else text, text=caption, font=F["t13"], bg=SURFACE, fg=MUTE,
                                anchor="w", justify="left")
        if caption:
            if beside:
                self.caption.pack(side="left", padx=(px(8), 0), pady=(px(2), 0))
            else:
                self.caption.pack(fill="x", pady=(px(4), 0))
        self.actions = tk.Frame(head, bg=SURFACE)
        self.actions.pack(side="right")
        self.body = tk.Frame(self.inner, bg=SURFACE)
        self.body.pack(fill="both", expand=True, pady=(px(16) if (title or caption) else 0, 0))


class Banner(RoundBox):
    """안내 상자: 상태색 테두리 + 옅은 바탕 + 아이콘. 제목 한 줄(14/600, 상태색), 설명 두 줄까지(13, 보조색).

    두 줄을 넘는 설명은 접어 두고 '자세히'에 마우스를 올리면 보여 준다. 버튼은 오른쪽(세로 가운데).
    종류: success · warning · danger · info · todo(파랑, 할 일) · neutral
    """

    LINES = 2

    def __init__(self, parent, **kw):
        super().__init__(parent, radius=px(12), fill=SURFACE, padx=px(14), pady=px(12), fit=True, **kw)
        self.tone = "neutral"
        row = tk.Frame(self.inner, bg=self.fill)
        row.pack(fill="x")
        self._row = row
        self.icon = Icon(row, "info", SUB)
        self.icon.pack(side="left", anchor="n", pady=(px(2), 0))
        text = tk.Frame(row, bg=self.fill)
        text.pack(side="left", fill="x", expand=True, padx=(px(10), 0))
        self._text = text
        self.title = tk.Label(text, font=F["t14s"], anchor="w", justify="left", bg=self.fill, fg=FG)
        self.title.pack(fill="x")
        self.detail = tk.Label(text, font=F["t13"], anchor="w", justify="left", bg=self.fill, fg=SUB)
        self.detail.pack(fill="x", pady=(px(2), 0))
        self.more = tk.Label(text, text="자세히", font=F["t12m"], anchor="w", bg=self.fill, fg=ACC_INK, cursor="hand2")
        self._more_text = ""
        self._tip = Tooltip(self)
        self.more.bind("<Enter>", lambda e: self._tip.show(e.x_root, e.y_root, self._more_text))
        self.more.bind("<Leave>", lambda e: self._tip.hide())
        self.actions = tk.Frame(row, bg=self.fill)
        self.actions.pack(side="right", padx=(px(16), 0))
        self.bind("<Configure>", self._wrap, add="+")

    def _wrap(self, _e=None) -> None:
        width = max(px(200), self.winfo_width() - 2 * self.padx - self.actions.winfo_reqwidth() - px(48))
        self.title.configure(wraplength=width)
        self.detail.configure(wraplength=width)

    def set(self, tone: str, title: str, detail: str = "") -> None:
        tone = "danger" if tone == "error" else tone
        self.tone = tone
        color = TONE_COLOR.get(tone, SUB)
        base = bg_of(self)
        if tone == "todo":
            fill, outline = mix(ACC, .16, base), mix(ACC_INK, .45, base)
        else:
            fill, outline = mix(color, .08, base), mix(color, .45, base)
        self.restyle(fill=fill, outline=outline)
        for w in (self._row, self._text, self.actions, self.title, self.detail, self.icon, self.more):
            w.configure(bg=fill)
        for child in self.actions.winfo_children():
            if isinstance(child, Button):
                child.set_bg(fill)
        self.icon.set(tone if tone != "neutral" else "info", color)
        lines = [line for line in detail.split("\n") if line.strip()] if detail else []
        shown, rest = lines[:self.LINES], lines[self.LINES:]
        self.title.configure(text=title, fg=color if title and tone != "neutral" else FG)
        self.detail.configure(text="\n".join(shown), fg=SUB if title else FG)
        self._more_text = "\n".join(rest)
        for w in (self.title, self.detail, self.more):
            w.pack_forget()
        if title:
            self.title.pack(fill="x")
        if shown:
            self.detail.pack(fill="x", pady=(px(2) if title else 0, 0))
        if rest:
            self.more.pack(anchor="w", pady=(px(2), 0))

    @property
    def full_detail(self) -> str:
        shown = self.detail.cget("text")
        return "\n".join(x for x in (shown, self._more_text) if x)


def page_header(parent, title: str, meta: str = ""):
    """화면 머리: 제목 22/600 + 회색 메타 13 + 오른쪽 도구. 패널 밖 한 줄(높이 40 이상)."""
    head = tk.Frame(parent, bg=bg_of(parent), height=px(40))
    head.title = tk.Label(head, text=title, font=F["t22s"], bg=bg_of(parent), fg=FG, anchor="w")
    head.title.pack(side="left")
    head.meta = tk.Label(head, text=meta, font=F["t13"], bg=bg_of(parent), fg=MUTE, anchor="w")
    head.meta.pack(side="left", padx=(px(16), 0), pady=(px(4), 0))
    head.tools = tk.Frame(head, bg=bg_of(parent))
    head.tools.pack(side="right")
    return head


# ====================================================================== 버튼·라벨
# 종류: 면, 글자, 테두리 / 올림: 면, 글자, 테두리 / 누름 면
_KINDS = {
    "primary": (ACC_FILL, ACC_ON, ACC_FILL, ACC_FILL_HOVER, ACC_ON, ACC_FILL_HOVER, ACC_FILL_PRESS),
    "secondary": (WELL, FG, LINE_2, CHIP, FG, SUB, PRESS),
    "strong": (WELL, FG, SUB, CHIP, FG, SUB, PRESS),
    "ghost": (None, SUB, None, HOVER, FG, None, PRESS),
    "danger": (None, DANGER, None, HOVER, DANGER, None, PRESS),
    "link": (None, ACC_INK, None, None, ACC_INK, None, None),
    "quiet": (None, FG, LINE, CHIP, FG, LINE_2, PRESS),
    "chip": (WELL, FG, LINE_2, CHIP, FG, SUB, PRESS),   # 칩: 알약 모양. 고르는 칩은 모두 같은 모양 (색으로 답을 암시하지 않음)
}
# 크기: 높이, 좌우 여백, 모서리, 글자
_SIZES = {"lg": (48, 20, 12, "t15"), "md": (40, 16, 12, "t14"), "sm": (32, 12, 6, "t13"), "xs": (28, 10, 6, "t13")}


class Button(tk.Canvas):
    """버튼: 주 행동(채운 파랑, 창마다 하나) · 기본 · 강한 테두리 · 글자 · 링크 · 조용한. 크기 lg 48 · md 40 · sm 32 · xs 28.

    상태는 기본 → 올림 → 누름 → 비활성. 비활성은 투명도 대신 요소 면 + 비활성 글자색.
    configure/cget의 text·state·command를 tk.Button처럼 받는다. Tab으로 포커스, Enter·Space로 누른다.
    """

    def __init__(self, parent, text: str, command=None, kind: str = "secondary", size: str = "md", width=None,
                 icon: str | None = None):
        if kind == "quiet":
            size = "xs"
        h, padx, radius, font = _SIZES.get(size, _SIZES["md"])
        self._h, self._padx, self._radius = px(h), px(padx), px(radius)
        if kind == "chip":
            self._padx, self._radius = px(14), px(h) / 2
        self._font = F[font + ("s" if kind == "primary" else "m")]
        self._fixed = width
        self._icon = icon
        super().__init__(parent, height=self._h, highlightthickness=0, bd=0, bg=bg_of(parent), cursor="hand2",
                         takefocus=1)
        self._text, self._command, self._kind, self._state = text, command, kind, "normal"
        self._hover = self._pressed = self._focus = False
        self._resize()
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<KeyPress-Return>", lambda e: self.invoke())
        self.bind("<KeyPress-space>", lambda e: self.invoke())

    def _content_width(self) -> int:
        width = _measure(self._font, self._text)
        if self._icon:
            width += px(16) + (px(6) if self._text else 0)
        return width

    def _resize(self) -> None:
        width = self._fixed or self._content_width() + 2 * self._padx
        super().configure(width=width)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        mapped = self.winfo_ismapped() and self.winfo_width() > 1
        w = self.winfo_width() if mapped else int(self.cget("width"))  # 늘어나거나 줄어든 실제 폭
        h = self._h
        fill, fg, border, hfill, hfg, hborder, pfill = _KINDS.get(self._kind, _KINDS["secondary"])
        if self._state == "disabled":
            fill, fg, border = (None if self._kind in ("ghost", "link", "danger", "quiet") else WELL), FAINT, None
        elif self._pressed:
            fill, fg, border = pfill or hfill or fill, hfg, hborder or border
        elif self._hover:
            fill, fg, border = hfill or fill, hfg, hborder or border
        if self._focus and self._state == "normal":
            border = FOCUS
        if fill or border:
            rounded_rect(self, 0.5, 0.5, w - 1.5, h - 1.5, self._radius, fill=fill or bg_of(self),
                         outline=border or fill or bg_of(self), width=2 if border == FOCUS else 1)
        cx = w / 2
        if self._icon:
            total = self._content_width()
            left = (w - total) / 2
            draw_icon(self, self._icon, left, (h - px(16)) / 2, px(16), fg)
            cx = left + px(16) + (px(6) if self._text else 0) + _measure(self._font, self._text) / 2
        if self._text:
            self.create_text(cx, h / 2, text=self._text, fill=fg, font=self._font)
            if self._kind == "link" and self._hover and self._state == "normal":
                tw = _measure(self._font, self._text)
                y = h / 2 + tkfont.Font(font=self._font).metrics("descent") + 1
                self.create_line(cx - tw / 2, y, cx + tw / 2, y, fill=fg)

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        if not value:
            self._pressed = False
        if self._state == "normal":
            self._draw()

    def _set_focus(self, value: bool) -> None:
        self._focus = value
        self._draw()

    def _press(self, _e) -> None:
        if self._state == "normal":
            self._pressed = True
            self._draw()

    def _release(self, e) -> None:
        inside = 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()
        fire = self._pressed and inside and self._state == "normal"
        self._pressed = False
        if self.winfo_exists():
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
    """라벨: 높이 20, 모서리 6, 12/500. 기본은 칩 바탕 + 보조 글자, 상태 라벨은 옅은 상태색 + 상태색 글자.
    한 행에 상태 라벨은 하나만 둔다."""

    TONES = {"neutral": (CHIP, SUB), "warning": (soft("warning"), WARN), "danger": (soft("danger"), DANGER),
             "success": (soft("success"), OK), "now": (ACC_TILE, ACC_INK)}

    def __init__(self, parent, text: str = "", tone: str = "neutral"):
        super().__init__(parent, height=px(20), highlightthickness=0, bd=0, bg=bg_of(parent))
        self._text, self._tone = text, tone
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if not self._text:
            super().configure(width=1)
            return
        fill, fg = self.TONES.get(self._tone, self.TONES["neutral"])
        w = _measure(F["t12m"], self._text) + px(12)
        h = px(20)
        super().configure(width=w)
        rounded_rect(self, 0, 0, w - 1, h - 1, px(6), fill=fill, outline=fill)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=F["t12m"])

    def set(self, text: str, tone: str = "neutral") -> None:
        self._text, self._tone = text, tone
        self._draw()

    def cget(self, key):
        return self._text if key == "text" else super().cget(key)


class StatusDot(tk.Frame):
    """상태 점: 8px 점 + 13px 글자. 빨강(위반·오류) · 초록(종결·켜짐) · 노랑(일시정지) · 회색(나머지)."""

    COLORS = {"danger": DANGER, "success": OK, "warning": WARN, "neutral": FAINT}

    def __init__(self, parent, text: str = "", tone: str = "neutral"):
        super().__init__(parent, bg=bg_of(parent))
        self.dot = tk.Canvas(self, width=px(8), height=px(8), highlightthickness=0, bd=0, bg=bg_of(parent))
        self.dot.pack(side="left", padx=(0, px(6)))
        self.label = tk.Label(self, text=text, font=F["t13"], bg=bg_of(parent), fg=SUB)
        self.label.pack(side="left")
        self.set(text, tone)

    def set(self, text: str, tone: str = "neutral") -> None:
        self._text = text
        self.dot.delete("all")
        self.dot.create_oval(0, 0, px(8) - 1, px(8) - 1, fill=self.COLORS.get(tone, FAINT), outline="")
        self.label.configure(text=text)

    def cget(self, key):
        return self._text if key == "text" else super().cget(key)


# ====================================================================== 입력
class _Field(RoundBox):
    """입력 테두리 5가지: 기본(채운 면·테두리 없음) → 올림(#59616B) → 쓰는 중(#C1C7CE) / 오류(빨강) / 성공(초록)."""

    def _setup_states(self, widget: tk.Misc) -> None:
        self._hovered = self._focused = False
        self._mark: str | None = None
        for w in (self, widget):
            w.bind("<Enter>", lambda e: self._state(hover=True), add="+")
            w.bind("<Leave>", lambda e: self._state(hover=False), add="+")
        widget.bind("<FocusIn>", lambda e: self._state(focus=True), add="+")
        widget.bind("<FocusOut>", lambda e: self._state(focus=False), add="+")

    def _state(self, hover=None, focus=None) -> None:
        if hover is not None:
            self._hovered = hover
        if focus is not None:
            self._focused = focus
        if self._mark == "error":
            outline = DANGER
        elif self._mark == "ok":
            outline = OK
        elif self._focused:
            outline = SUB
        elif self._hovered:
            outline = LINE_2
        else:
            outline = ""
        self.restyle(outline=outline, width_px=1)

    def set_state(self, mark: str | None) -> None:
        """'error' · 'ok' · None"""
        self._mark = mark
        self._state()

    # 예전 이름
    def watch(self, widget: tk.Misc) -> None:
        self._setup_states(widget)


_Focusable = _Field


def scrolled_text(parent, **kw) -> tk.Text:
    """문장 상자: 요소 면(#343A40), 모서리 12, 16px·줄 간격 1.65. 어두운 스크롤바. Text를 돌려준다."""
    box = _Field(parent, radius=px(12), fill=WELL, outline=None, padx=px(8), pady=px(8))
    box.pack(fill="both", expand=True)
    text = tk.Text(box.inner, wrap="word", font=F["t16"], relief="flat", bd=0, highlightthickness=0,
                   bg=WELL, fg=FG, insertbackground=FG, selectbackground=ACC_TILE, selectforeground=FG,
                   padx=px(8), pady=px(6), spacing1=px(3), spacing2=px(5), spacing3=px(3), **kw)
    bar = ttk.Scrollbar(box.inner, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    box.watch(text)
    text.box = box
    return text


class Input(_Field):
    """한 줄 입력: 높이 40, 모서리 12, 요소 면. 라벨은 13/500 (따로 둔다)."""

    def __init__(self, parent, textvariable=None, **kw):
        super().__init__(parent, radius=px(12), fill=WELL, outline=None, padx=px(12), pady=px(8),
                         height=px(40), **kw)
        self.entry = tk.Entry(self.inner, textvariable=textvariable, font=F["t14"], relief="flat", bd=0,
                              highlightthickness=0, bg=WELL, fg=FG, insertbackground=FG,
                              selectbackground=ACC_TILE, selectforeground=FG, disabledbackground=WELL,
                              disabledforeground=FAINT)
        self.entry.pack(fill="both", expand=True)
        self.watch(self.entry)


def field_note(parent, text: str, tone: str = "info") -> tk.Frame:
    """칸 아래 문구: 안내(ⓘ 회색) · 오류(ⓧ 빨강) · 성공(✓ 초록) 아이콘을 앞에 붙인다. 12px."""
    row = tk.Frame(parent, bg=bg_of(parent))
    color = {"info": MUTE, "error": DANGER, "danger": DANGER, "success": OK}.get(tone, MUTE)
    Icon(row, {"info": "info", "error": "danger", "danger": "danger", "success": "success"}.get(tone, "info"),
         color, 16).pack(side="left", anchor="n")
    row.label = tk.Label(row, text=text, font=F["t12m" if tone in ("error", "danger") else "t12"],
                         bg=bg_of(parent), fg=color, anchor="w", justify="left")
    row.label.pack(side="left", padx=(px(4), 0))
    return row


class Select(Button):
    """선택칸: 입력과 같은 요소 면(테두리는 올리면 보임) + 오른쪽 화살표. 누르면 어두운 메뉴로 고른다.
    Combobox처럼 get/current/set."""

    def __init__(self, parent, values, size: str = "md"):
        self._values = list(values)
        self._value = self._values[0] if self._values else ""
        super().__init__(parent, self._value, self._open, kind="secondary", size=size)

    def _resize(self) -> None:
        width = max((_measure(self._font, v) for v in self._values), default=0) + self._padx + px(40)
        tk.Canvas.configure(self, width=width)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        mapped = self.winfo_ismapped() and self.winfo_width() > 1
        w, h = (self.winfo_width() if mapped else int(self.cget("width"))), self._h
        border = FOCUS if self._focus else (LINE_2 if self._hover and self._state == "normal" else WELL)
        rounded_rect(self, 0.5, 0.5, w - 1.5, h - 1.5, self._radius, fill=WELL, outline=border,
                     width=2 if border == FOCUS else 1)
        fg = FG if self._state == "normal" else FAINT
        self.create_text(self._padx, h / 2, text=self._text, fill=fg, font=self._font, anchor="w")
        draw_icon(self, "chevron-down", w - px(28), (h - px(16)) / 2, px(16), MUTE)

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
    """토글: 36×22. 켜면 트랙 #365989·손잡이 #DCE8FA(줄지어 있어도 튀지 않는 낮은 파랑),
    끄면 요소 면 + #59616B 테두리."""

    def __init__(self, parent, text: str, variable: tk.BooleanVar, command=None):
        super().__init__(parent, bg=bg_of(parent))
        self.var, self.command = variable, command
        self.canvas = tk.Canvas(self, width=px(36), height=px(22), highlightthickness=0, bd=0, bg=bg_of(parent),
                                cursor="hand2")
        self.canvas.pack(side="left")
        self.label = tk.Label(self, text=text, font=F["t14"], bg=bg_of(parent), fg=FG, cursor="hand2")
        self.label.pack(side="left", padx=(px(10), 0))
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
        w, h, on = px(36), px(22), self.var.get()
        if on:
            rounded_rect(c, 0.5, 0.5, w - 1.5, h - 1.5, h / 2, fill=SWITCH_ON, outline=SWITCH_ON)
        else:
            rounded_rect(c, 0.5, 0.5, w - 1.5, h - 1.5, h / 2, fill=WELL, outline=LINE_2)
        d = px(18)
        pad = (h - d) / 2
        x = w - pad - d if on else pad
        c.create_oval(x, pad, x + d - 1, pad + d - 1, fill=SWITCH_THUMB_ON if on else SURFACE, outline="")


class Check(tk.Canvas):
    """체크박스: 18px, 모서리 4. 켜면 #3D7EF7로 채우고 흰 체크."""

    def __init__(self, parent, variable: tk.BooleanVar, command=None):
        super().__init__(parent, width=px(18), height=px(18), highlightthickness=0, bd=0, bg=bg_of(parent),
                         cursor="hand2")
        self.var, self.command = variable, command
        self.bind("<Button-1>", self._toggle)
        variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _toggle(self, _e=None) -> None:
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self) -> None:
        self.delete("all")
        n = px(18)
        if self.var.get():
            rounded_rect(self, 0.5, 0.5, n - 1.5, n - 1.5, px(4), fill=ACC, outline=ACC)
            draw_icon(self, "check", px(2), px(2), px(14), ACC_ON)
        else:
            rounded_rect(self, 0.75, 0.75, n - 1.75, n - 1.75, px(4), fill=SURFACE, outline=LINE_2, width=1.5)


class Segmented(tk.Frame):
    """탭·필터: 알약 칩(높이 32, 테두리 #59616B). 고른 칩은 요소 면 + 본문 글자. 파랑은 쓰지 않는다."""

    def __init__(self, parent, options, variable: tk.StringVar, command=None):
        super().__init__(parent, bg=bg_of(parent))
        self.var, self.command, self.items = variable, command, []
        self._hover = None
        for value, label in options:
            w = _measure(F["t13m"], label) + px(28)
            c = tk.Canvas(self, width=w, height=px(32), highlightthickness=0, bd=0, bg=bg_of(parent), cursor="hand2")
            c.pack(side="left", padx=(0, px(6)))
            c.bind("<Button-1>", lambda e, v=value: self._choose(v))
            c.bind("<Enter>", lambda e, v=value: self._set_hover(v))
            c.bind("<Leave>", lambda e: self._set_hover(None))
            self.items.append((value, label, c))
        variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _set_hover(self, value) -> None:
        self._hover = value
        self._draw()

    def _choose(self, value: str) -> None:
        if self.var.get() != value:
            self.var.set(value)
            if self.command:
                self.command()

    def _draw(self) -> None:
        for value, label, c in self.items:
            c.delete("all")
            w, h = int(c.cget("width")), px(32)
            chosen = self.var.get() == value
            if chosen:
                fill, outline, fg = WELL, WELL, FG
            elif self._hover == value:
                fill, outline, fg = HOVER, LINE_2, FG
            else:
                fill, outline, fg = bg_of(self), LINE_2, SUB
            rounded_rect(c, 0.5, 0.5, w - 1.5, h - 1.5, h / 2, fill=fill, outline=outline)
            c.create_text(w / 2, h / 2, text=label, font=F["t13m"], fill=fg)


# ====================================================================== 떠 있는 것: 메뉴·툴팁·알림·대화상자
def menu(parent) -> tk.Menu:
    return tk.Menu(parent, tearoff=0, bg=SURFACE, fg=FG, activebackground=CHIP, activeforeground=FG,
                   disabledforeground=MUTE, selectcolor=ACC, font=F["t14"], relief="flat", bd=1,
                   activeborderwidth=0)


_KEY = "#010203"  # 창 모서리를 투명하게 할 색 (Windows)


def floating(master, width: int, height: int, radius: int, fill: str, outline: str | None):
    """둥근 모서리의 떠 있는 창. Windows에서는 모서리를 투명하게, 다른 OS에서는 화면 바탕색으로."""
    win = tk.Toplevel(master)
    win.wm_overrideredirect(True)
    win.attributes("-topmost", True)
    key = BG
    if sys.platform == "win32":
        try:
            win.attributes("-transparentcolor", _KEY)
            key = _KEY
        except tk.TclError:
            pass
    win.configure(bg=key)
    c = tk.Canvas(win, width=width, height=height, highlightthickness=0, bd=0, bg=key)
    c.pack()
    rounded_rect(c, 0.5, 0.5, width - 1.5, height - 1.5, radius, fill=fill, outline=outline or fill, tags="shape")
    return win, c


_floating = floating  # 예전 이름


class Tooltip:
    """툴팁: 패널 면 + 선(그림자 대신), 모서리 12, 13px."""

    def __init__(self, master):
        self.master = master
        self.win = None

    def show(self, x: int, y: int, text: str) -> None:
        self.hide()
        if not text:
            return
        f = tkfont.Font(font=F["t13"])
        lines = text.split("\n")
        w = min(px(420), max(f.measure(line) for line in lines) + px(24))
        h = f.metrics("linespace") * len(lines) + px(16)
        self.win, c = floating(self.master, w, h, px(12), SURFACE, LINE)
        c.create_text(px(12), px(8), text=text, anchor="nw", fill=FG, font=F["t13"], width=w - px(24))
        self.win.geometry(f"+{x + px(14)}+{y + px(18)}")

    def hide(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None


TOAST_ICON = {"success": "success", "warning": "warning", "error": "danger", "danger": "danger", "info": "info"}


def toast(master, message: str, ms: int = 3500, tone: str = "success", action: tuple | None = None):
    """알림: 화면 위 가운데. 패널 면에 상태색 테두리·아이콘, 글자는 본문색 14/500, 모서리 12.
    종류는 완료(success)·경고(warning)·오류(error). action=(글자, 단축키, 함수)면 오른쪽에 버튼과
    남은 시간 막대를 둔다 (되돌리기 Z, 10초)."""
    tone = "error" if tone == "danger" else tone
    color = TONE_COLOR.get(tone, OK)
    fill, border = mix(color, .10, SURFACE), mix(color, .50, SURFACE)
    f = tkfont.Font(font=F["t14m"])
    limit = min(px(640), master.winfo_screenwidth() - px(64))
    btn_w = 0
    if action:
        label, key, _ = action
        btn_w = _measure(F["t13m"], label) + (_measure(F["t12"], key) + px(14) if key else 0) + px(28)
    text_w = min(f.measure(message), limit - btn_w - px(60))
    lines = max(1, -(-f.measure(message) // max(1, text_w)))
    w = text_w + btn_w + px(14 + 16 + 10 + 16)
    h = max(px(44), lines * f.metrics("linespace") + px(20))
    win, c = floating(master, w, h, px(12), fill, border)
    draw_icon(c, TOAST_ICON.get(tone, "success"), px(14), (h - px(16)) / 2, px(16), color)
    c.create_text(px(14 + 16 + 10), h / 2, text=message, fill=FG, font=F["t14m"], anchor="w", width=text_w)
    if action:
        label, key, command = action
        bx1, by1, bx2, by2 = w - px(8) - btn_w, (h - px(30)) / 2, w - px(8), (h + px(30)) / 2
        btn_fill, btn_hover = mix(FG, .08, fill), mix(FG, .14, fill)
        body = rounded_rect(c, bx1, by1, bx2, by2, px(6), fill=btn_fill, outline=btn_fill, tags="btn")
        c.create_text(bx1 + px(10), h / 2, text=label, fill=FG, font=F["t13m"], anchor="w", tags="btn")
        if key:
            kx = bx2 - px(10) - _measure(F["t12"], key) - px(8)
            rounded_rect(c, kx, h / 2 - px(9), bx2 - px(10), h / 2 + px(9), px(4), fill=btn_fill, outline=LINE_2,
                         tags="btn")
            c.create_text((kx + bx2 - px(10)) / 2, h / 2, text=key, fill=SUB, font=F["t12"], tags="btn")
        c.tag_bind("btn", "<Enter>", lambda e: c.itemconfigure(body, fill=btn_hover, outline=btn_hover))
        c.tag_bind("btn", "<Leave>", lambda e: c.itemconfigure(body, fill=btn_fill, outline=btn_fill))
        c.tag_bind("btn", "<ButtonRelease-1>", lambda e: command())
        bar = c.create_line(px(12), h - px(3), w - px(12), h - px(3), fill=mix(color, .5, fill), width=2)
        total, started = ms / 1000, time.monotonic()

        def shrink():
            if not win.winfo_exists():
                return
            left = max(0.0, 1 - (time.monotonic() - started) / total)
            c.coords(bar, px(12), h - px(3), px(12) + (w - px(24)) * left, h - px(3))
            if left > 0:
                win.after(100, shrink)

        shrink()
    x = (master.winfo_screenwidth() - w) // 2
    y = px(48)
    win.geometry(f"+{x}+{y}")
    return win


class Dialog(tk.Toplevel):
    """대화상자: 폭 520(넓은 것 640), 패널 면 + 선 테두리, 제목 18/600, 설명 13 보조색.
    .body(여백 18/22)와 .buttons(위쪽 선, 오른쪽 정렬)."""

    def __init__(self, master, title: str, desc: str = "", wide: bool = False):
        super().__init__(master)
        self.title(title)
        self.configure(bg=LINE, padx=1, pady=1)  # 1px 선 (그림자 대신)
        self.resizable(False, False)
        self.transient(master)
        width = px(640 if wide else 520)
        self.minsize(width, 1)
        frame = tk.Frame(self, bg=SURFACE)
        frame.pack(fill="both", expand=True)
        head = tk.Frame(frame, bg=SURFACE, padx=px(22), pady=px(20))
        head.pack(fill="x")
        tk.Label(head, text=title, font=F["t18s"], bg=SURFACE, fg=FG, anchor="w").pack(fill="x")
        if desc:
            tk.Label(head, text=desc, font=F["t13"], bg=SURFACE, fg=SUB, anchor="w", justify="left",
                     wraplength=width - px(44)).pack(fill="x", pady=(px(4), 0))
        self.body = tk.Frame(frame, bg=SURFACE, width=width - px(44), padx=px(22))
        self.body.pack(fill="both", expand=True, pady=(0, px(18)))
        tk.Frame(frame, bg=LINE, height=1).pack(fill="x")
        foot = tk.Frame(frame, bg=SURFACE, padx=px(22), pady=px(14))
        foot.pack(fill="x")
        self.buttons = foot


# ====================================================================== 표
def table(parent, columns, height: int = 6) -> ttk.Treeview:
    """표: 머리 12/500 회색(바탕 없음), 행 40, 14px. 올리면 #2E3439, 고르면 선택 면. 오른쪽에 어두운 스크롤바."""
    frame = tk.Frame(parent, bg=bg_of(parent))
    frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=height, selectmode="none")
    for cid, label_text, width in columns:
        tree.heading(cid, text=label_text, anchor="w")
        tree.column(cid, width=px(width), anchor="w", stretch=cid == columns[-1][0])
    bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    tree.tag_configure("muted", foreground=MUTE)
    tree.tag_configure("warning", foreground=WARN)
    tree.tag_configure("danger", foreground=DANGER)
    tree.tag_configure("hover", background=HOVER)
    hovered = {"row": ""}

    def move(event):
        row = tree.identify_row(event.y)
        if row == hovered["row"]:
            return
        old = hovered["row"]
        if old and tree.exists(old):
            tree.item(old, tags=tuple(t for t in tree.item(old, "tags") if t != "hover"))
        if row:
            tree.item(row, tags=tuple(tree.item(row, "tags")) + ("hover",))
        hovered["row"] = row

    def leave(_e):
        old = hovered["row"]
        if old and tree.exists(old):
            tree.item(old, tags=tuple(t for t in tree.item(old, "tags") if t != "hover"))
        hovered["row"] = ""

    tree.bind("<Motion>", move, add="+")
    tree.bind("<Leave>", leave, add="+")
    return tree


def label(parent, text: str = "", style: str = "t13", fg: str = MUTE, **kw) -> tk.Label:
    return tk.Label(parent, text=text, font=F[style], bg=bg_of(parent), fg=fg, anchor="w", justify="left", **kw)
