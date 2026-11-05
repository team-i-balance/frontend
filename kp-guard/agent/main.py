"""진입점: python -m agent.main [--background]

--background : 자동 시작용. 트레이가 있으면 창 없이 상주한다.
"""
from __future__ import annotations

import argparse
import ctypes
import sys


def _dpi_aware() -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="KP Guard — 생성형 AI 입력정보 사전 점검 도우미")
    parser.add_argument("--background", action="store_true", help="창 없이 트레이에 상주")
    args = parser.parse_args(argv)

    from .system import win32
    if not win32.acquire_single_instance():
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        if not args.background:
            messagebox.showinfo("KP Guard", "이미 실행 중이에요. 트레이 아이콘이나 Ctrl+Alt+O로 열 수 있어요.")
        root.destroy()
        return

    _dpi_aware()
    from .gui.app import App
    App(background=args.background).run()


if __name__ == "__main__":
    main()
