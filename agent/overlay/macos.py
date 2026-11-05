"""Read the active macOS accessibility element and paint nonactivating AppKit panels."""

import os
import threading
import time

import AppKit as K
import ApplicationServices as A
import objc
from CoreFoundation import CFHash

from ..server.client import MAX_TEXT
from .source import OWN_WINDOW, Draft, Snapshot, clip, composer, end_only, site, terminal_draft

try:
    import Quartz as Q
except ImportError:  # pyobjc-framework-Quartz: Enter guard and typed edits
    Q = None

BROWSERS = {
    "com.apple.safari",
    "com.apple.safaritechnologypreview",
    "com.google.chrome",
    "com.google.chrome.canary",
    "com.microsoft.edgemac",
    "org.mozilla.firefox",
    "com.brave.browser",
    "com.naver.whale",
    "company.thebrowser.browser",
    "com.vivaldi.vivaldi",
    "com.operasoftware.opera",
}
TERMINALS = {"com.apple.terminal", "com.googlecode.iterm2"}


def attribute(element, name):
    error, value = A.AXUIElementCopyAttributeValue(element, name, None)
    return value if not error else None


def parameter(element, name, arg):
    error, value = A.AXUIElementCopyParameterizedAttributeValue(element, name, arg, None)
    return value if not error else None


def unpack(value, kind):
    if value is not None:
        success, obj = A.AXValueGetValue(value, kind, None)
        if success:
            return obj
    return None


def ax_range(start, length):
    return A.AXValueCreate(A.kAXValueCFRangeType, (start, length))


def rect(frame):
    (x, y), (width, height) = frame
    return round(x), round(y), round(x + width), round(y + height)


def boundary(element):
    pos = unpack(attribute(element, "AXPosition"), A.kAXValueCGPointType)
    size = unpack(attribute(element, "AXSize"), A.kAXValueCGSizeType)
    return rect((pos, size)) if pos is not None and size is not None else (0, 0, 0, 0)


def ancestors(field):
    for _ in range(48):
        if field is None:
            break
        yield field
        field = attribute(field, "AXParent")


def web_url(doc):
    url = attribute(doc, "AXURL")
    return str(url.absoluteString() if hasattr(url, "absoluteString") else url or "")


def is_editor(field, role):
    if role not in ("AXTextArea", "AXTextField") or attribute(field, "AXSubrole") == "AXSecureTextField":
        return False
    classes = attribute(field, "AXDOMClassList") or ()
    label = (
        attribute(field, "AXTitle")
        or attribute(field, "AXDescription")
        or attribute(field, "AXPlaceholderValue")
    )
    return composer(attribute(field, "AXDOMIdentifier"), " ".join(classes), label, role == "AXTextArea")


def utf16_offsets(raw):
    offsets = [0]
    for char in raw:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
    return offsets


def boxes(field, start, end):
    def bounds(a, b):
        value = parameter(field, "AXBoundsForRange", ax_range(a, b - a))
        frame = unpack(value, A.kAXValueCGRectType)
        return rect(frame) if frame is not None else None

    # AXBoundsForRange returns a union for wrapped lines. Split by native visual line when available.
    line = parameter(field, "AXLineForIndex", start)
    while line is not None and start < end:
        extent = unpack(parameter(field, "AXRangeForLine", line), A.kAXValueCFRangeType)
        if extent is None or not extent[0] <= start < extent[0] + extent[1]:
            break
        stop = min(end, extent[0] + extent[1])
        region = bounds(start, stop)
        if region:
            yield region
        start, line = stop, line + 1
    if start < end:
        first, last = bounds(start, start + 1), bounds(end - 1, end)
        if first and last and first[1:4:2] == last[1:4:2]:
            region = bounds(start, end)
            if region:
                yield region
        else:
            # Some web views implement only AXBoundsForRange. Merge touching glyphs on each row.
            previous = None
            for index in range(start, end):
                part = bounds(index, index + 1)
                if not part or part[2] <= part[0]:
                    continue
                if previous and previous[1:4:2] == part[1:4:2] and previous[0] <= part[0] <= previous[2] + 1:
                    previous = previous[0], previous[1], max(previous[2], part[2]), previous[3]
                else:
                    if previous:
                        yield previous
                    previous = part
            if previous:
                yield previous


class Reader:
    pool = staticmethod(objc.autorelease_pool)

    def __enter__(self):
        self.current_pid = None
        self.reset()
        return self

    def __exit__(self, *args):
        self.reset()

    def reset(self):
        self.saved = None

    @staticmethod
    def focus(key):
        """Before typing: re-activate the editor's app if a chip click activated ours."""
        return activate(key[0])

    def sample(self):
        if not A.AXIsProcessTrusted():
            self.reset()
            raise PermissionError("손쉬운 사용 권한이 필요합니다.")
        app = K.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid, bundle = app.processIdentifier(), str(app.bundleIdentifier()).lower()
        if pid == os.getpid():
            return OWN_WINDOW
        if bundle not in BROWSERS | TERMINALS:
            self.reset()
            return None
        if pid != self.current_pid:
            self.reset()
            self.current_pid, self.app = pid, A.AXUIElementCreateApplication(pid)
            A.AXUIElementSetMessagingTimeout(self.app, 0.3)
            if bundle in BROWSERS and not bundle.startswith(("com.apple.", "org.mozilla.")):
                A.AXUIElementSetAttributeValue(self.app, "AXManualAccessibility", True)
        focus = attribute(self.app, "AXFocusedUIElement")
        if focus is None:
            return None
        chain = [(n, attribute(n, "AXRole")) for n in ancestors(focus)]
        window = attribute(self.app, "AXFocusedWindow")
        if window is None:
            return None
        if bundle in TERMINALS:
            self.reset()
            field = next((n for n, role in chain if role == "AXTextArea"), None)
            if field is None:
                return None
            visible = unpack(attribute(field, "AXVisibleCharacterRange"), A.kAXValueCFRangeType)
            if visible is None or not 0 < visible[1] <= 100_000:
                return None
            raw = parameter(field, "AXStringForRange", ax_range(visible[0], visible[1]))
            if not isinstance(raw, str):
                return None
            draft = terminal_draft(raw, attribute(window, "AXTitle") or "")
            if draft is not None:
                return self.snapshot(pid, "cli", window, field, raw, visible[0], draft)
            return None

        docs = [n for n, role in chain if role == "AXWebArea"]
        typing = False  # Enter is guarded only while the keyboard focus is in the composer itself
        if docs:
            field = next((n for n, role in chain if is_editor(n, role)), None)
            typing = field is not None
            if field is not None:
                urls = [web_url(doc) for doc in docs]
                if not all(site(url) and site(url) == site(urls[0]) for url in urls):
                    self.reset()
                    return None
                self.saved = window, docs[0], urls[0], field
        if not self.saved:
            return None
        owner, doc, url, field = self.saved
        if (
            window != owner
            or web_url(doc) != url
            or (docs and docs[0] != doc)
            or not attribute(field, "AXParent")
        ):
            self.reset()
            return None
        if not docs:
            # A toolbar click may also mean a tab switched. Hit-test the cached editor in the front window.
            x, y, _, b = boundary(field)
            error, hit = A.AXUIElementCopyElementAtPosition(self.app, x + 2, (y + b) / 2, None)
            if error or not any(n == doc for n in ancestors(hit)):
                self.reset()
                return None
        raw = attribute(field, "AXValue")
        if isinstance(raw, str) and len(raw) <= MAX_TEXT:
            return self.snapshot(pid, site(url), window, field, raw, 0, Draft.plain(raw), url, typing)
        return None

    @staticmethod
    def snapshot(pid, kind, window, field, raw, base, draft, url="", focused=True):
        viewport = clip(boundary(field), boundary(window))

        offsets = None

        def ranges(a, b):
            nonlocal offsets
            if viewport is None:
                return
            if offsets is None:
                offsets = utf16_offsets(raw)
            for start, end in draft.parts(a, b):
                for part in boxes(field, base + offsets[start], base + offsets[end]):
                    visible = clip(part, viewport)
                    if visible:
                        yield visible

        def replace(a, b, new):
            nonlocal offsets
            if not activate(pid):
                return False
            if kind == "cli":
                # A TUI owns its caret. Retype only the value at the end of the prompt.
                if not end_only(draft, a, b):
                    return False
                return press(51, len(draft.text[a:])) and type_text(new)  # kVK_Delete (Backspace)
            parts = list(draft.parts(a, b))
            if len(parts) != 1:
                return False
            if offsets is None:
                offsets = utf16_offsets(raw)
            start, end = parts[0]
            selection = ax_range(base + offsets[start], offsets[end] - offsets[start])
            if A.AXUIElementSetAttributeValue(field, "AXSelectedTextRange", selection):
                return False
            time.sleep(0.03)
            if not A.AXUIElementSetAttributeValue(field, "AXSelectedText", new):
                return True
            return type_text(new) if new else press(51)

        bounds = viewport or ()
        return Snapshot((pid, kind, CFHash(window), CFHash(field), url), draft, ranges, replace, bounds, focused)


class Paint:
    def __init__(self):
        self.panels = []

    @staticmethod
    def top():
        return K.NSScreen.screens()[0].frame().size.height

    def draw(self, regions):
        # Signal blue #3D7EF7: a light fill plus a 2px underline per range.
        shapes = []
        for x, y, r, b in regions:
            shapes += [(x, y, r, b, 0.25), (x, max(y, b - 2), r, b, 0.92)]
        for i, (x, y, r, b, alpha) in enumerate(shapes):
            frame = K.NSMakeRect(x, self.top() - b, r - x, b - y)
            if i == len(self.panels):
                panel = K.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                    frame, K.NSWindowStyleMaskNonactivatingPanel, K.NSBackingStoreBuffered, False
                )
                panel.setReleasedWhenClosed_(False)
                panel.setOpaque_(False)
                panel.setHasShadow_(False)
                panel.setHidesOnDeactivate_(False)
                panel.setIgnoresMouseEvents_(True)
                panel.setLevel_(K.NSFloatingWindowLevel)
                panel.setCollectionBehavior_(
                    K.NSWindowCollectionBehaviorCanJoinAllSpaces
                    | K.NSWindowCollectionBehaviorFullScreenAuxiliary
                )
                panel.setBackgroundColor_(
                    K.NSColor.colorWithSRGBRed_green_blue_alpha_(0.239, 0.494, 0.969, alpha)
                )
                self.panels.append(panel)
            panel = self.panels[i]
            panel.setBackgroundColor_(K.NSColor.colorWithSRGBRed_green_blue_alpha_(0.239, 0.494, 0.969, alpha))
            panel.setFrame_display_(frame, True)
            panel.orderFrontRegardless()
        for panel in self.panels[len(shapes) :]:
            panel.orderOut_(None)

    def pointer(self):
        p = K.NSEvent.mouseLocation()
        return round(p.x), round(self.top() - p.y), bool(K.NSEvent.pressedMouseButtons() & 1)

    def screen_at(self, x, y):
        top = self.top()
        for screen in K.NSScreen.screens():
            if K.NSPointInRect(K.NSMakePoint(x, top - y), screen.frame()):
                left, low, right, high = rect(screen.visibleFrame())
                return left, round(top - high), right, round(top - low)
        return rect(K.NSScreen.mainScreen().frame())

    def close(self):
        for panel in self.panels:
            panel.close()


def request_access():
    A.AXIsProcessTrustedWithOptions({A.kAXTrustedCheckOptionPrompt: True})


def activate(pid, wait=0.5):
    """Bring the editor's app to the front (a click on our chip activates this Python app on macOS)."""
    front = K.NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is not None and front.processIdentifier() == pid:
        return True
    app = K.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    app.activateWithOptions_(K.NSApplicationActivateIgnoringOtherApps)
    until = time.monotonic() + wait
    while time.monotonic() < until:
        front = K.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None and front.processIdentifier() == pid:
            return True
        time.sleep(0.02)
    return False


def give_back(pid):
    """칩을 띄우며 이 앱이 앞으로 왔으면 입력창의 앱을 다시 앞으로 (기다리지 않음)."""
    activate(pid, wait=0)


MAGIC = 0x4B50  # kCGEventSourceUserData of our own keystrokes


def _post(events):
    for event in events:
        Q.CGEventSetIntegerValueField(event, Q.kCGEventSourceUserData, MAGIC)
        Q.CGEventPost(Q.kCGHIDEventTap, event)
        time.sleep(0.004)
    return True


def type_text(text):
    if Q is None:
        return False
    events = []
    for char in text:
        for down in (True, False):
            event = Q.CGEventCreateKeyboardEvent(None, 0, down)
            Q.CGEventKeyboardSetUnicodeString(event, len(char.encode("utf-16-le")) // 2, char)
            events.append(event)
    return _post(events)


def press(code, times=1):
    if Q is None:
        return False
    return _post([Q.CGEventCreateKeyboardEvent(None, code, down) for _ in range(times) for down in (True, False)])


class EnterGuard:
    """While highlights remain, drop a plain Return (send) going to that editor's app. Shift+Return still passes.

    target() returns the guarded process id or None. blocked() runs on the tap thread and must only post.
    Requires the same Accessibility permission as the highlights.
    """

    def __init__(self, target, blocked):
        if Q is None:
            raise OSError("Quartz unavailable")
        self.target, self.blocked = target, blocked
        self.tap = self.loop = None
        self.swallowed = False

    def start(self):
        ready = threading.Event()

        def run():
            mask = Q.CGEventMaskBit(Q.kCGEventKeyDown) | Q.CGEventMaskBit(Q.kCGEventKeyUp)
            self.tap = Q.CGEventTapCreate(Q.kCGAnnotatedSessionEventTap, Q.kCGHeadInsertEventTap,
                                          Q.kCGEventTapOptionDefault, mask, self._handle, None)
            if not self.tap:
                ready.set()
                return
            source = Q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            self.loop = Q.CFRunLoopGetCurrent()
            Q.CFRunLoopAddSource(self.loop, source, Q.kCFRunLoopCommonModes)
            Q.CGEventTapEnable(self.tap, True)
            ready.set()
            Q.CFRunLoopRun()

        threading.Thread(target=run, daemon=True, name="enter-guard").start()
        ready.wait(2)
        if not self.tap:
            raise OSError("event tap unavailable (Accessibility permission)")

    def _handle(self, proxy, kind, event, refcon):
        try:
            if kind in (Q.kCGEventTapDisabledByTimeout, Q.kCGEventTapDisabledByUserInput):
                Q.CGEventTapEnable(self.tap, True)
                return event
            if Q.CGEventGetIntegerValueField(event, Q.kCGEventSourceUserData) == MAGIC:
                return event
            if Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode) not in (36, 76):
                return event
            if kind == Q.kCGEventKeyDown:
                target = self.target()
                if not target or Q.CGEventGetFlags(event) & Q.kCGEventFlagMaskShift:
                    return event
                pid = Q.CGEventGetIntegerValueField(event, Q.kCGEventTargetUnixProcessID)
                if not pid:
                    front = K.NSWorkspace.sharedWorkspace().frontmostApplication()
                    pid = front.processIdentifier() if front is not None else 0
                if pid == target:
                    self.swallowed = True
                    self.blocked()
                    return None
            elif kind == Q.kCGEventKeyUp and self.swallowed:
                self.swallowed = False
                return None
        except Exception:
            pass
        return event

    def stop(self):
        if self.loop is not None:
            Q.CFRunLoopStop(self.loop)
