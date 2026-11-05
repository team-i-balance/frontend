"""임시 UI 테마. UI는 이후 교체 예정이라 색·글꼴만 한곳에 모아 둔다."""
import tkinter as tk
from tkinter import ttk

FAMILY = "Malgun Gothic"
FONT = (FAMILY, 10)
SMALL = (FAMILY, 9)
BOLD = (FAMILY, 10, "bold")
TITLE = (FAMILY, 13, "bold")
TEXT = (FAMILY, 11)

BG = "#f6f7f9"
PRIMARY = "#2563eb"
MUTED = "#6b7280"

# 등급 배너 (배경, 글자)
GRADE = {
    "O": ("#e8f5e9", "#1b5e20"),
    "S": ("#fff4e5", "#8a4b00"),
    "C": ("#fdecea", "#b71c1c"),
}

# 하이라이트 색 (span.CATEGORY)
HIGHLIGHT = {
    "identifier": "#ffcdd2",
    "finance": "#ffe0b2",
    "contact": "#fff59d",
    "secret": "#ffab91",
    "person": "#c8e6c9",
    "quasi": "#bbdefb",
    "org": "#e1bee7",
    "blocker": "#ef9a9a",
    "manual": "#b2dfdb",
}
REVERTED_BG = "#e5e7eb"
SUSPECT_BG = "#fffbea"
SUSPECT_FG = "#b45309"
RESTORED_FG = "#1d4ed8"


def setup(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.configure(".", font=FONT)
    style.configure("Treeview", rowheight=24, font=FONT)
    style.configure("Treeview.Heading", font=BOLD)
    style.configure("TLabelframe.Label", font=BOLD)
    root.option_add("*Font", FONT)


def primary_button(parent, text, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command, bg=PRIMARY, fg="white", activebackground="#1d4ed8",
                     activeforeground="white", relief="flat", font=BOLD, padx=16, pady=6, cursor="hand2",
                     disabledforeground="#c7d2fe")


def plain_button(parent, text, command, small=False) -> tk.Button:
    return tk.Button(parent, text=text, command=command, relief="groove", bg="white",
                     font=SMALL if small else FONT, padx=10, pady=4 if not small else 2, cursor="hand2")


def scrolled_text(parent, **kw) -> tk.Text:
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, wrap="word", font=TEXT, relief="flat", padx=8, pady=8, bg="white",
                   highlightthickness=1, highlightbackground="#d1d5db", **kw)
    bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    return text


def index(n: int) -> str:
    """파이썬 문자열 위치 → Tk Text 인덱스"""
    return f"1.0 + {n} chars"
