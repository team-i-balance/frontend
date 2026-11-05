"""Windows 로그인 시 자동 시작 (HKCU\\...\\Run). 관리자 권한 불필요, 현재 사용자에만 적용."""
from __future__ import annotations

import sys
import plistlib
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "KPGuard"

try:
    import winreg
except ImportError:  # Windows 외
    winreg = None


def command() -> str:
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return f'"{sys.executable}" --background'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # 콘솔 창 없이 실행
    if pythonw.exists():
        exe = pythonw
    launcher = Path(__file__).resolve().parents[2] / "kp_guard.pyw"
    return f'"{exe}" "{launcher}" --background'


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.ibalance.kpguard.plist"


def _mac_arguments() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--background"]
    launcher = Path(__file__).resolve().parents[2] / "kp_guard.pyw"
    return [sys.executable, str(launcher), "--background"]


def is_enabled() -> bool:
    if sys.platform == "darwin":
        try:
            with _launch_agent_path().open("rb") as f:
                data = plistlib.load(f)
            return data.get("ProgramArguments") == _mac_arguments() and Path(_mac_arguments()[0]).exists() and (getattr(sys, "frozen", False) or Path(_mac_arguments()[1]).exists())
        except (OSError, ValueError, plistlib.InvalidFileException):
            return False
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False


def enable() -> None:
    if sys.platform == "darwin":
        path = _launch_agent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            plistlib.dump({"Label": "com.ibalance.kpguard", "ProgramArguments": _mac_arguments(), "RunAtLoad": True}, f)
        return
    if winreg is None:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())


def disable() -> None:
    if sys.platform == "darwin":
        _launch_agent_path().unlink(missing_ok=True)
        return
    if winreg is None:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
