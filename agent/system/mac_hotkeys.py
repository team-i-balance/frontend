"""Global macOS shortcuts via a listen-only Quartz event tap."""
import threading

try:
    import Quartz as Q
except ImportError:
    Q = None

# macOS hardware keycodes for the four application shortcuts.
_KEYCODES = {35: 1, 15: 2, 31: 3, 3: 4}

class HotkeyThread(threading.Thread):
    def __init__(self, bindings, callback):
        super().__init__(daemon=True, name='macos-hotkeys')
        self.bindings, self.callback = bindings, callback
        self.registered, self.failed = [], []
        self._loop = None
        self._tap = None
        self._stopping = False

    def run(self):
        if Q is None:
            self.failed = [value[2] for value in self.bindings.values()]
            return
        def event_handler(proxy, event_type, event, refcon):
            if event_type in (Q.kCGEventTapDisabledByTimeout, Q.kCGEventTapDisabledByUserInput):
                if self._tap and not self._stopping:
                    Q.CGEventTapEnable(self._tap, True)
                return event
            flags = Q.CGEventGetFlags(event)
            needed = Q.kCGEventFlagMaskCommand | Q.kCGEventFlagMaskAlternate
            if flags & needed == needed and not flags & (Q.kCGEventFlagMaskShift | Q.kCGEventFlagMaskControl):
                key = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
                repeat = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventAutorepeat)
                if key in _KEYCODES and not repeat:
                    self.callback(_KEYCODES[key])
            return event
        self._tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap,
                                        Q.kCGEventTapOptionListenOnly, Q.CGEventMaskBit(Q.kCGEventKeyDown), event_handler, None)
        if self._tap is None:
            self.failed = [value[2] for value in self.bindings.values()]
            return
        source = Q.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = Q.CFRunLoopGetCurrent()
        Q.CFRunLoopAddSource(self._loop, source, Q.kCFRunLoopCommonModes)
        self.registered = [value[2] for value in self.bindings.values()]
        Q.CGEventTapEnable(self._tap, True)
        if not self._stopping:
            Q.CFRunLoopRun()
        Q.CGEventTapEnable(self._tap, False)
        self._loop = None

    def stop(self):
        self._stopping = True
        if Q is not None and self._loop is not None:
            Q.CFRunLoopStop(self._loop)
