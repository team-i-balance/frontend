"""Read the frontmost AI draft via UI Automation and paint click-through Win32 windows."""

import ctypes as C
import ntpath
import os
from collections import deque
from ctypes import wintypes as W

import uiautomation as u
from comtypes.client import CreateObject, GetModule

from ..server.client import MAX_TEXT
from .source import OWN_WINDOW, Draft, Snapshot, clip, composer, site, terminal_draft

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
TERMINALS = {"windowsterminal.exe", "openconsole.exe", "conhost.exe"}


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


def boxes(text_range, raw, a, b):
    """UIA text units need not match Python code points (emoji, CRLF). Verify the exact occurrence."""
    rest = text_range.Clone()
    needle = raw[a:b]
    if not needle:
        return []
    # Advance past earlier occurrences so repeated phone numbers map independently.
    cursor = 0
    while cursor <= a:
        expected = raw.find(needle, cursor)
        match = rest.FindText(needle, False, False)
        if expected < 0 or expected > a or not match:
            return []
        if expected == a:
            # Providers can normalize whitespace/case. Never paint an unverified offset.
            prefix = text_range.Clone()
            prefix.MoveEndpointByRange(1, match, 0, 0)
            if prefix.GetText(-1) != raw[:a] or match.GetText(-1) != needle:
                return []
            return [rect(r) for r in match.GetBoundingRectangles()]
        cursor = expected + len(needle)
        rest.MoveEndpointByRange(0, match, 1, 0)
    return []


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
        if exe not in BROWSERS | TERMINALS:
            return None
        focused = u.GetFocusedControl()
        top = focused.GetTopLevelControl() if focused else None
        lineage = list(ancestry(focused)) if top and top.NativeWindowHandle == hwnd else []
        if exe in TERMINALS:
            self.reset()
            for field in lineage:
                if field.ControlTypeName not in (
                    "TextControl",
                    "DocumentControl",
                    "EditControl",
                    "PaneControl",
                ):
                    continue
                pattern = field.GetPattern(u.PatternId.TextPattern)
                if pattern:
                    for view in reversed(pattern.GetVisibleRanges()):
                        raw = view.GetText(100001)
                        draft = terminal_draft(raw, u.GetWindowText(hwnd))
                        if draft is not None:
                            return self.snapshot(hwnd, "cli", field, view, raw, draft)
            return None

        docs = [n for n in lineage if n.ControlTypeName == "DocumentControl"]
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
                return self.snapshot(hwnd, site(url), field, view, raw, Draft.plain(raw), url)
        return None

    @staticmethod
    def snapshot(hwnd, kind, field, view, raw, draft, url=""):
        viewport = rect(field.BoundingRectangle)

        def ranges(a, b):
            for start, end in draft.parts(a, b):
                for region in boxes(view, raw, start, end):
                    visible = clip(region, viewport)
                    if visible:
                        yield visible

        return Snapshot((hwnd, kind, tuple(field.GetRuntimeId()), url), draft, ranges)


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
        self.name = "KPGuardTextMark"
        self.instance = api(kernel, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)(None)
        self.brush = api(gdi, "CreateSolidBrush", W.HBRUSH, W.DWORD)(0x23A6F6)
        wc = WNDCLASS(
            proc=C.cast(user.DefWindowProcW, C.c_void_p),
            instance=self.instance,
            brush=self.brush,
            name=self.name,
        )
        if not api(user, "RegisterClassW", W.ATOM, C.POINTER(WNDCLASS))(C.byref(wc)):
            raise C.WinError(C.get_last_error())
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
        for i, (x, y, r, b) in enumerate(regions):
            if i == len(self.handles):
                # WS_EX_LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE + WS_POPUP.
                hwnd = self.create(
                    0x080800A0, self.name, "", 0x80000000, x, y, r - x, b - y, None, None, self.instance, None
                )
                if not hwnd:
                    raise C.WinError(C.get_last_error())
                self.layer(hwnd, 0, 85, 2)
                self.handles.append(hwnd)
            self.move(self.handles[i], -1, x, y, r - x, b - y, 0x0050)
        for hwnd in self.handles[len(regions) :]:
            self.show(hwnd, 0)

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
