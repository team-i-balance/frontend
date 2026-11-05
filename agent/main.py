"""진입점: python -m agent.main [--background]

--background : 자동 시작용. 트레이가 있으면 창 없이 상주한다.
"""
from __future__ import annotations

import os

# 외부 연결 없음: onnxruntime의 마이크로소프트 원격 수집은 onnxruntime을 불러오기 전에 이 값이 있어야 꺼진다
# (backend#13). agent/detect/model.py도 넣지만, 진입점에서 먼저 넣어 어느 모듈이 먼저 불러와도 꺼지게 한다.
# 이미 지정한 값은 덮어쓰지 않는다 (팀 서버와 같은 방식).
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

import argparse  # noqa: E402
import ctypes  # noqa: E402
import sys  # noqa: E402


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

    from .system import platform_api
    if not platform_api.acquire_single_instance():
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
