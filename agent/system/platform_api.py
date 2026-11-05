"""Platform boundary for clipboard, hotkeys and single-instance handling.

The Windows implementation remains the existing adapter. Other platforms expose
an explicit unsupported status until their native implementation is installed.
"""
import sys

from . import win32 as _windows

MOD_ALT = _windows.MOD_ALT
MOD_CONTROL = _windows.MOD_CONTROL
MOD_SHIFT = _windows.MOD_SHIFT

if sys.platform == 'win32':
    acquire_single_instance = _windows.acquire_single_instance
    clipboard_sequence = _windows.clipboard_sequence
    get_clipboard_text = _windows.get_clipboard_text
    set_clipboard_text = _windows.set_clipboard_text
    own_window_in_foreground = _windows.own_window_in_foreground
    HotkeyThread = _windows.HotkeyThread
else:
    import threading

    def acquire_single_instance(name='KPGuard'):
        return True

    def clipboard_sequence():
        return 0

    def get_clipboard_text():
        return None

    def set_clipboard_text(text):
        return False

    def own_window_in_foreground():
        return False

    class HotkeyThread(threading.Thread):
        def __init__(self, bindings, callback):
            super().__init__(daemon=True, name='unsupported-hotkeys')
            self.registered = []
            self.failed = [binding[2] for binding in bindings.values()]

        def run(self):
            pass

        def stop(self):
            pass

if sys.platform == "darwin":
    from . import mac_clipboard as _clipboard
    clipboard_sequence = _clipboard.clipboard_sequence
    get_clipboard_text = _clipboard.get_clipboard_text
    set_clipboard_text = _clipboard.set_clipboard_text
    own_window_in_foreground = _clipboard.own_window_in_foreground
