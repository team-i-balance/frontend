"""Read the frontmost AI draft via UI Automation and paint click-through Win32 windows."""

import ctypes as C
import ntpath
import os
import threading
import time
from collections import deque
from ctypes import wintypes as W

import uiautomation as u
from comtypes.client import CreateObject, GetModule

from ..server.client import MAX_TEXT
from .source import OWN_WINDOW, Draft, Snapshot, clip, composer, site

user = C.WinDLL("user32", use_last_error=True)
kernel = C.WinDLL("kernel32", use_last_error=True)
gdi = C.WinDLL("gdi32", use_last_error=True)


def api(dll, name, result, *args):
    proc = getattr(dll, name)
    proc.argtypes, proc.restype = args, result
    return proc


foreground = api(user, "GetForegroundWindow", W.HWND)
pid_for = api(user, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.POINTER(W.DWORD))
open_process = api(kernel, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
process_image = api(
    kernel, "QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)
)
close_handle = api(kernel, "CloseHandle", W.BOOL, W.HANDLE)
BROWSERS = {
    "chrome.exe",
    "msedge.exe",
    "firefox.exe",
    "brave.exe",
    "whale.exe",
    "vivaldi.exe",
    "opera.exe",
    "arc.exe",
}


def executable(pid):
    handle = open_process(0x1000, False, pid)
    if handle:
        try:
            path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            if process_image(handle, 0, path, C.byref(size)):
                return ntpath.basename(path.value).lower()
        finally:
            close_handle(handle)
    return ""


def rect(value):
    return value.left, value.top, value.right, value.bottom


def ancestry(control):
    for _ in range(48):
        if control is None:
            break
        yield control
        control = control.GetParentControl()


def document_url(doc):
    for ident in (u.PatternId.ValuePattern, u.PatternId.LegacyIAccessiblePattern):
        pattern = doc.GetPattern(ident)
        if pattern and pattern.Value:
            return pattern.Value
    return ""


def address_bar(hwnd):
    # Some UIA bridges omit Document.Value. Search browser chrome only, never a page's own textboxes.
    queue = deque([u.ControlFromHandle(hwnd)])
    count = 0
    while queue and count < 200:
        node = queue.popleft()
        if not node:
            continue
        count += 1
        role = node.ControlTypeName
        if role == "DocumentControl" or node.IsOffscreen:
            continue
        if role == "EditControl" and (
            node.AutomationId in ("addressEditBox", "urlbar-input")
            or node.Name.lower()
            in ("address and search bar", "search or enter address", "주소 및 검색창", "주소 및 검색 표시줄")
        ):
            return node
        child = node.GetFirstChildControl()
        while child and len(queue) + count < 200:
            queue.append(child)
            child = child.GetNextSiblingControl()
    return None


def is_editor(field):
    role = field.ControlTypeName
    if role != "EditControl" and field.GetPropertyValue(u.PropertyId.AriaRoleProperty) != "textbox":
        return False
    if field.IsPassword or field.IsOffscreen or not field.IsEnabled:
        return False
    value = field.GetPattern(u.PatternId.ValuePattern)
    if value and value.IsReadOnly:
        return False
    aria = str(field.GetPropertyValue(u.PropertyId.AriaPropertiesProperty)).lower()
    return composer(field.AutomationId, field.ClassName, field.Name, "multiline=true" in aria)


def editor_range(field, doc):
    own = field.GetPattern(u.PatternId.TextPattern)
    if own:
        return own.DocumentRange
    child = field.GetPattern(u.PatternId.TextChildPattern)
    if child:
        return child.TextRange
    # The wrapper's RangeFromChild currently refers to the class instead of the argument.
    parent = doc.GetPattern(u.PatternId.TextPattern)
    if parent:
        raw = parent.pattern.RangeFromChild(field.Element)
        if raw:
            return u.TextRange(raw)
    return None


def exact_range(text_range, raw, a, b):
    """UIA text units need not match Python code points (emoji, CRLF). Verify the exact occurrence."""
    rest = text_range.Clone()
    needle = raw[a:b]
    if not needle:
        return None
    # Advance past earlier occurrences so repeated phone numbers map independently.
    cursor = 0
    while cursor <= a:
        expected = raw.find(needle, cursor)
        match = rest.FindText(needle, False, False)
        if expected < 0 or expected > a or not match:
            return None
        if expected == a:
            # Providers can normalize whitespace/case. Never paint or edit an unverified offset.
            if match.GetText(-1) != needle:
                return None
            if raw.count(needle) > 1:
                prefix = text_range.Clone()
                prefix.MoveEndpointByRange(1, match, 0, 0)
                if prefix.GetText(-1) != raw[:a]:
                    return None
            return match
        cursor = expected + len(needle)
        rest.MoveEndpointByRange(0, match, 1, 0)
    return None


def boxes(text_range, raw, a, b):
    match = exact_range(text_range, raw, a, b)
    return [rect(r) for r in match.GetBoundingRectangles()] if match else []


# ---------------------------------------------------------------- typed input (in-place edits)
ULONG_PTR = C.c_size_t
INJECTED = 0x4B50  # dwExtraInfo of our own keystrokes ("KP")


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class _INPUTUNION(C.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _fields_ = [("type", W.DWORD), ("u", _INPUTUNION)]


send_input = api(user, "SendInput", W.UINT, W.UINT, C.POINTER(INPUT), C.c_int)
set_foreground = api(user, "SetForegroundWindow", W.BOOL, W.HWND)


def _key(vk=0, scan=0, flags=0):
    return INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, INJECTED)))


def keystrokes(events):
    events = list(events)
    if not events:
        return True
    array = (INPUT * len(events))(*events)
    return send_input(len(events), array, C.sizeof(INPUT)) == len(events)


def type_text(text):
    """Type characters as Unicode keystrokes (bypasses the IME and keyboard layout)."""
    units = text.encode("utf-16-le")
    events = []
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i:i + 2], "little")
        events += [_key(scan=code, flags=0x0004), _key(scan=code, flags=0x0004 | 0x0002)]  # UNICODE, KEYUP
    return keystrokes(events)


def press(vk, times=1):
    return keystrokes([e for _ in range(times) for e in (_key(vk=vk), _key(vk=vk, flags=0x0002))])


def bring_to_front(hwnd, wait=0.5):
    if foreground() == hwnd:
        return True
    set_foreground(hwnd)
    until = time.monotonic() + wait
    while time.monotonic() < until:
        if foreground() == hwnd:
            return True
        time.sleep(0.02)
    return foreground() == hwnd


def give_back(hwnd):
    """칩을 띄우며 포커스를 가져왔으면 입력창 창에 돌려준다 (기다리지 않음)."""
    if foreground() != hwnd:
        set_foreground(hwnd)


def no_activate(window):
    """Chip clicks must not take focus from the AI editor (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)."""
    hwnd = int(window.wm_frame(), 16)
    get = api(user, "GetWindowLongPtrW", C.c_ssize_t, W.HWND, C.c_int)
    put = api(user, "SetWindowLongPtrW", C.c_ssize_t, W.HWND, C.c_int, C.c_ssize_t)
    put(hwnd, -20, get(hwnd, -20) | 0x08000000 | 0x00000080)


class Reader:
    def __enter__(self):
        self.com = u.UIAutomationInitializerInThread()
        self.saved = None
        self.bar = (None, None)
        automation = CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=GetModule("UIAutomationCore.dll").IUIAutomation,
        )
        self.editables = automation.CreateOrCondition(
            automation.CreatePropertyCondition(u.PropertyId.ControlTypeProperty, u.ControlType.EditControl),
            automation.CreatePropertyCondition(u.PropertyId.AriaRoleProperty, "textbox"),
        )
        self.reason = "foreground"
        return self

    def __exit__(self, *args):
        self.saved = None
        self.com.Uninitialize()

    def reset(self):
        self.saved = None

    @staticmethod
    def focus(key):
        """Before typing: give the foreground back to the editor's window if a chip click took it."""
        return bring_to_front(key[0])

    def location(self, doc, hwnd, use_bar):
        url = document_url(doc)
        if "://" in url or url.startswith(("about:", "chrome:", "edge:")) or not use_bar:
            return url
        if hwnd != self.bar[0] or self.bar[1] is None:
            self.bar = hwnd, address_bar(hwnd)
        bar = self.bar[1]
        if bar and not bar.HasKeyboardFocus:
            url = document_url(bar).strip()
            if "://" not in url:
                url = "https://" + url
            return url
        return ""

    def page_under_window(self, hwnd):
        bounds = W.RECT()
        api(user, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))(hwnd, C.byref(bounds))
        x = (bounds.left + bounds.right) // 2
        for fraction in (0.60, 0.83, 0.40):
            y = int(bounds.top + fraction * (bounds.bottom - bounds.top))
            hit = u.ControlFromPoint(x, y)
            if hit:
                top = hit.GetTopLevelControl()
                if top and top.NativeWindowHandle == hwnd:
                    chain = list(ancestry(hit))
                    if any(n.ControlTypeName == "DocumentControl" for n in chain):
                        return chain
        return []

    def find_editor(self, doc):
        # Providers may leave focus on the web document or a text child during IME / DOM updates.
        # Filter on the provider side, then require the editor to belong to this exact document.
        found = doc.Element.FindAll(4, self.editables)
        matches = []
        for i in range(min(found.Length, 50)):
            field = u.Control.CreateControlFromElement(found.GetElement(i))
            if is_editor(field):
                owner = next(
                    (n for n in ancestry(field.GetParentControl()) if n.ControlTypeName == "DocumentControl"),
                    None,
                )
                if owner and owner.GetRuntimeId() == doc.GetRuntimeId():
                    matches.append(field)
        return matches[0] if len(matches) == 1 else None

    def sample(self):
        self.reason = "foreground"
        hwnd, pid = foreground(), W.DWORD()
        if not hwnd:
            return None
        pid_for(hwnd, C.byref(pid))
        if pid.value == os.getpid():
            return OWN_WINDOW
        exe = executable(pid.value)
        if exe not in BROWSERS:
            return None
        focused = u.GetFocusedControl()
        top = focused.GetTopLevelControl() if focused else None
        lineage = list(ancestry(focused)) if top and top.NativeWindowHandle == hwnd else []
        docs = [n for n in lineage if n.ControlTypeName == "DocumentControl"]
        in_focus = bool(docs)  # the keyboard focus is inside the page (not the address bar or other chrome)
        # After clicking page chrome the focused UIA element often belongs to the address bar.
        # Resolve the page under the foreground window and keep scanning its original composer.
        if not docs:
            lineage = self.page_under_window(hwnd)
            docs = [n for n in lineage if n.ControlTypeName == "DocumentControl"]
        self.reason = "web-document"
        if not docs:
            return None
        doc = docs[0]
        urls = [self.location(d, hwnd, len(docs) == 1) for d in docs]
        self.reason = "verified-origin"
        if not all(site(url) and site(url) == site(urls[0]) for url in urls):
            self.reset()
            return None
        url = urls[0]
        doc_id = tuple(doc.GetRuntimeId())
        candidate = next((n for n in lineage[: lineage.index(doc)] if is_editor(n)), None)
        # Enter is guarded only while the keyboard focus is in the composer itself.
        typing = in_focus and candidate is not None
        cache_key = hwnd, url, doc_id
        if candidate is None and self.saved and self.saved[0] == cache_key:
            previous = self.saved[1]
            if not previous.IsOffscreen:
                candidate = previous
        if candidate is None:
            candidate = self.find_editor(doc)
        self.reason = "prompt-editor"
        if candidate is None:
            self.reset()
            return None
        self.saved = cache_key, candidate
        field, owner = candidate, doc
        self.reason = "prompt-text-range"
        view = editor_range(field, owner)
        if view:
            raw = view.GetText(MAX_TEXT + 1)
            if len(raw) <= MAX_TEXT:
                return self.snapshot(hwnd, site(url), field, view, raw, Draft.plain(raw), url, typing)
        return None

    @staticmethod
    def snapshot(hwnd, kind, field, view, raw, draft, url="", focused=True):
        viewport = rect(field.BoundingRectangle)

        def ranges(a, b):
            for start, end in draft.parts(a, b):
                for region in boxes(view, raw, start, end):
                    visible = clip(region, viewport)
                    if visible:
                        yield visible

        def replace(a, b, new):
            if foreground() != hwnd and not bring_to_front(hwnd):
                return False
            parts = list(draft.parts(a, b))
            if len(parts) != 1:
                return False
            match = exact_range(view, raw, *parts[0])
            if match is None:
                return False
            try:
                match.Select(waitTime=0)
            except TypeError:  # older uiautomation without waitTime
                match.Select()
            time.sleep(0.03)
            return type_text(new) if new else press(0x08)

        return Snapshot((hwnd, kind, tuple(field.GetRuntimeId()), url), draft, ranges, replace, viewport, focused)


class WNDCLASS(C.Structure):
    _fields_ = [
        ("style", W.UINT),
        ("proc", C.c_void_p),
        ("classExtra", C.c_int),
        ("windowExtra", C.c_int),
        ("instance", W.HINSTANCE),
        ("icon", W.HICON),
        ("cursor", W.HANDLE),
        ("brush", W.HBRUSH),
        ("menu", W.LPCWSTR),
        ("name", W.LPCWSTR),
    ]


class Paint:
    def __init__(self):
        self.handles = []
        self.alphas = []
        self.name = "KPGuardTextMark"
        self.instance = api(kernel, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)(None)
        # Signal blue #3D7EF7 (COLORREF is 0x00BBGGRR): a light fill plus a 2px underline per range.
        self.brush = api(gdi, "CreateSolidBrush", W.HBRUSH, W.DWORD)(0xF77E3D)
        wc = WNDCLASS(
            proc=C.cast(user.DefWindowProcW, C.c_void_p),
            instance=self.instance,
            brush=self.brush,
            name=self.name,
        )
        register = api(user, "RegisterClassW", W.ATOM, C.POINTER(WNDCLASS))
        if not register(C.byref(wc)):
            error = C.get_last_error()
            # A previous overlay in this process left the class registered: replace it with this brush.
            unregister = api(user, "UnregisterClassW", W.BOOL, W.LPCWSTR, W.HINSTANCE)
            if error != 1410 or not unregister(self.name, self.instance) or not register(C.byref(wc)):
                raise C.WinError(error)
        self.create = api(
            user,
            "CreateWindowExW",
            W.HWND,
            W.DWORD,
            W.LPCWSTR,
            W.LPCWSTR,
            W.DWORD,
            C.c_int,
            C.c_int,
            C.c_int,
            C.c_int,
            W.HWND,
            W.HMENU,
            W.HINSTANCE,
            C.c_void_p,
        )
        self.move = api(
            user, "SetWindowPos", W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT
        )
        self.layer = api(user, "SetLayeredWindowAttributes", W.BOOL, W.HWND, W.DWORD, W.BYTE, W.DWORD)
        self.show = api(user, "ShowWindow", W.BOOL, W.HWND, C.c_int)
        self.destroy = api(user, "DestroyWindow", W.BOOL, W.HWND)

    def draw(self, regions):
        shapes = []
        for x, y, r, b in regions:
            shapes.append((x, y, r, b, 64))                       # fill ~25%
            shapes.append((x, max(y, b - 2), r, b, 235))          # underline
        for i, (x, y, r, b, alpha) in enumerate(shapes):
            if i == len(self.handles):
                # WS_EX_LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE + WS_POPUP.
                hwnd = self.create(
                    0x080800A0, self.name, "", 0x80000000, x, y, r - x, b - y, None, None, self.instance, None
                )
                if not hwnd:
                    raise C.WinError(C.get_last_error())
                self.handles.append(hwnd)
                self.alphas.append(None)
            if self.alphas[i] != alpha:
                self.layer(self.handles[i], 0, alpha, 2)
                self.alphas[i] = alpha
            self.move(self.handles[i], -1, x, y, r - x, b - y, 0x0050)
        for hwnd in self.handles[len(shapes) :]:
            self.show(hwnd, 0)

    @staticmethod
    def chip_surface():
        return ChipSurface()

    def pointer(self):
        point = W.POINT()
        user.GetCursorPos(C.byref(point))
        return point.x, point.y, bool(user.GetAsyncKeyState(1) & 0x8000)

    @staticmethod
    def screen_at(x, y):
        class Monitor(C.Structure):
            _fields_ = [("size", W.DWORD), ("bounds", W.RECT), ("work", W.RECT), ("flags", W.DWORD)]

        handle = api(user, "MonitorFromPoint", W.HANDLE, W.POINT, W.DWORD)(W.POINT(x, y), 2)
        info = Monitor(size=C.sizeof(Monitor))
        api(user, "GetMonitorInfoW", W.BOOL, W.HANDLE, C.POINTER(Monitor))(handle, C.byref(info))
        return rect(info.work)

    def close(self):
        for hwnd in self.handles:
            self.destroy(hwnd)
        api(user, "UnregisterClassW", W.BOOL, W.LPCWSTR, W.HINSTANCE)(self.name, self.instance)
        api(gdi, "DeleteObject", W.BOOL, W.HANDLE)(self.brush)


class BITMAPINFOHEADER(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("width", W.LONG),
        ("height", W.LONG),
        ("planes", W.WORD),
        ("bits", W.WORD),
        ("compression", W.DWORD),
        ("image", W.DWORD),
        ("xppm", W.LONG),
        ("yppm", W.LONG),
        ("used", W.DWORD),
        ("important", W.DWORD),
    ]


class BLENDFUNCTION(C.Structure):
    _fields_ = [("op", C.c_ubyte), ("flags", C.c_ubyte), ("alpha", C.c_ubyte), ("format", C.c_ubyte)]


class ChipSurface:
    """A per-pixel-alpha window for the chips. It takes clicks (so the page below never gets them)
    but never becomes the foreground window, so the browser keeps the caret in the prompt."""

    NAME = "KPGuardChip"
    registered = False

    def __init__(self):
        self.instance = api(kernel, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)(None)
        if not ChipSurface.registered:
            hand = api(user, "LoadCursorW", W.HANDLE, W.HINSTANCE, C.c_void_p)(None, C.c_void_p(32649))
            wc = WNDCLASS(
                proc=C.cast(user.DefWindowProcW, C.c_void_p), instance=self.instance, cursor=hand, name=self.NAME
            )
            if not api(user, "RegisterClassW", W.ATOM, C.POINTER(WNDCLASS))(C.byref(wc)):
                error = C.get_last_error()
                if error != 1410:  # already registered
                    raise C.WinError(error)
            ChipSurface.registered = True
        create = api(
            user,
            "CreateWindowExW",
            W.HWND,
            W.DWORD,
            W.LPCWSTR,
            W.LPCWSTR,
            W.DWORD,
            C.c_int,
            C.c_int,
            C.c_int,
            C.c_int,
            W.HWND,
            W.HMENU,
            W.HINSTANCE,
            C.c_void_p,
        )
        # WS_EX_LAYERED | TOOLWINDOW | NOACTIVATE | TOPMOST, WS_POPUP. Not TRANSPARENT: it takes clicks.
        self.hwnd = create(0x08080088, self.NAME, "", 0x80000000, 0, 0, 1, 1, None, None, self.instance, None)
        if not self.hwnd:
            raise C.WinError(C.get_last_error())
        self.screen = api(user, "GetDC", W.HDC, W.HWND)(None)
        self.dc = api(gdi, "CreateCompatibleDC", W.HDC, W.HDC)(self.screen)
        self.section = api(
            gdi, "CreateDIBSection", W.HBITMAP, W.HDC, C.c_void_p, W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD
        )
        self.select = api(gdi, "SelectObject", W.HGDIOBJ, W.HDC, W.HGDIOBJ)
        self.delete = api(gdi, "DeleteObject", W.BOOL, W.HGDIOBJ)
        self.update = api(
            user,
            "UpdateLayeredWindow",
            W.BOOL,
            W.HWND,
            W.HDC,
            C.POINTER(W.POINT),
            C.POINTER(W.SIZE),
            W.HDC,
            C.POINTER(W.POINT),
            W.DWORD,
            C.POINTER(BLENDFUNCTION),
            W.DWORD,
        )
        self.move = api(user, "SetWindowPos", W.BOOL, W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT)
        self.bitmap, self.bits, self.size, self.shown = None, C.c_void_p(), (0, 0), False

    def present(self, image, x, y, alpha=255):
        w, h = image.size
        if (w, h) != self.size:
            info = BITMAPINFOHEADER(C.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = C.c_void_p()
            bitmap = self.section(self.dc, C.byref(info), 0, C.byref(bits), None, 0)
            if not bitmap:
                raise C.WinError(C.get_last_error())
            self.select(self.dc, bitmap)
            if self.bitmap:
                self.delete(self.bitmap)
            self.bitmap, self.bits, self.size = bitmap, bits, (w, h)
        data = image.convert("RGBa").tobytes("raw", "BGRa")
        C.memmove(self.bits, data, len(data))
        blend = BLENDFUNCTION(0, 0, max(0, min(255, alpha)), 1)
        self.update(
            self.hwnd, self.screen, C.byref(W.POINT(x, y)), C.byref(W.SIZE(w, h)), self.dc,
            C.byref(W.POINT(0, 0)), 0, C.byref(blend), 2,
        )
        # Stay above the word marks (also topmost) without taking focus.
        self.move(self.hwnd, -1, 0, 0, 0, 0, 0x0013 | (0 if self.shown else 0x0040))
        self.shown = True

    def close(self):
        api(user, "DestroyWindow", W.BOOL, W.HWND)(self.hwnd)
        api(gdi, "DeleteDC", W.BOOL, W.HDC)(self.dc)
        if self.bitmap:
            self.delete(self.bitmap)
        api(user, "ReleaseDC", C.c_int, W.HWND, W.HDC)(None, self.screen)


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD), ("time", W.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = C.WINFUNCTYPE(W.LPARAM, C.c_int, W.WPARAM, W.LPARAM)
call_next_hook = api(user, "CallNextHookEx", W.LPARAM, W.HHOOK, C.c_int, W.WPARAM, W.LPARAM)


class EnterGuard:
    """While highlights remain, swallow a plain Enter (send) in that editor's window. Shift+Enter still adds a line.

    target() returns the guarded window handle or None. blocked() runs on the hook thread and must only post.
    """

    def __init__(self, target, blocked):
        self.target, self.blocked = target, blocked
        self.hook = None
        self.thread_id = None
        self.swallowed = False
        self._proc = HOOKPROC(self._handle)

    def start(self):
        ready = threading.Event()

        def run():
            self.thread_id = kernel.GetCurrentThreadId()
            install = api(user, "SetWindowsHookExW", W.HHOOK, C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD)
            module = api(kernel, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)(None)
            self.hook = install(13, self._proc, module, 0)  # WH_KEYBOARD_LL
            ready.set()
            if not self.hook:
                return
            msg = W.MSG()
            get = api(user, "GetMessageW", W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
            while get(C.byref(msg), None, 0, 0) > 0:
                user.TranslateMessage(C.byref(msg))
                user.DispatchMessageW(C.byref(msg))
            api(user, "UnhookWindowsHookEx", W.BOOL, W.HHOOK)(self.hook)
            self.hook = None

        threading.Thread(target=run, daemon=True, name="enter-guard").start()
        ready.wait(2)
        if not self.hook:
            raise OSError("keyboard hook unavailable")

    def _handle(self, code, wparam, lparam):
        try:
            if code == 0:
                info = C.cast(lparam, C.POINTER(KBDLLHOOKSTRUCT)).contents
                if info.vkCode == 0x0D and not info.flags & 0x10:  # VK_RETURN, not injected
                    if wparam in (0x0100, 0x0104):  # WM_KEYDOWN, WM_SYSKEYDOWN
                        target = self.target()
                        shift = user.GetAsyncKeyState(0x10) & 0x8000
                        if target and not shift and foreground() == target:
                            self.swallowed = True
                            self.blocked()
                            return 1
                    elif wparam in (0x0101, 0x0105) and self.swallowed:
                        self.swallowed = False
                        return 1
        except Exception:
            pass
        return call_next_hook(None, code, wparam, lparam)

    def stop(self):
        if self.thread_id:
            api(user, "PostThreadMessageW", W.BOOL, W.DWORD, W.UINT, W.WPARAM, W.LPARAM)(self.thread_id, 0x0012, 0, 0)
