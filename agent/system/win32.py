"""Windows API 래퍼 (ctypes, 표준 라이브러리만): 단일 인스턴스, 클립보드, 전역 단축키, 포그라운드 창."""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
ERROR_ALREADY_EXISTS = 183

_mutex = None


# ---------------------------------------------------------------- 단일 인스턴스
def acquire_single_instance(name: str = "Local\\KPGuard_SingleInstance") -> bool:
    global _mutex
    if not IS_WINDOWS:
        return True
    _mutex = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


# ---------------------------------------------------------------- 클립보드
def _open_clipboard() -> bool:
    for _ in range(10):  # 다른 프로그램이 잡고 있으면 잠깐 기다린다
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def clipboard_sequence() -> int:
    return user32.GetClipboardSequenceNumber() if IS_WINDOWS else 0


def get_clipboard_text() -> str | None:
    if not IS_WINDOWS or not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    if not IS_WINDOWS or not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


def own_window_in_foreground() -> bool:
    """우리 창에서 복사한 것이면 다시 점검 창을 띄우지 않는다."""
    if not IS_WINDOWS:
        return False
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


# ---------------------------------------------------------------- 전역 단축키
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012


class HotkeyThread(threading.Thread):
    """RegisterHotKey는 등록한 스레드의 메시지 큐로 WM_HOTKEY를 보낸다 → 전용 스레드에서 루프."""

    def __init__(self, bindings: dict[int, tuple[int, int, str]], callback):
        super().__init__(daemon=True, name="hotkeys")
        self.bindings = bindings
        self.callback = callback
        self.failed: list[str] = []
        self.registered: list[str] = []
        self._tid = 0

    def run(self) -> None:
        if not IS_WINDOWS:
            return
        self._tid = kernel32.GetCurrentThreadId()
        for hid, (mods, vk, label) in self.bindings.items():
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self.registered.append(label)
            else:
                self.failed.append(label)  # 다른 프로그램이 이미 사용 중
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.callback(int(msg.wParam))
        for hid in self.bindings:
            user32.UnregisterHotKey(None, hid)

    def stop(self) -> None:
        if IS_WINDOWS and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
