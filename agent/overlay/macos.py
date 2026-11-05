"""Read the active macOS accessibility element and paint nonactivating AppKit panels."""

import os

import AppKit as K
import ApplicationServices as A
import objc
from CoreFoundation import CFHash

from ..server.client import MAX_TEXT
from .source import OWN_WINDOW, Draft, Snapshot, clip, composer, site, terminal_draft

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
        if docs:
            field = next((n for n, role in chain if is_editor(n, role)), None)
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
            return self.snapshot(pid, site(url), window, field, raw, 0, Draft.plain(raw), url)
        return None

    @staticmethod
    def snapshot(pid, kind, window, field, raw, base, draft, url=""):
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

        return Snapshot((pid, kind, CFHash(window), CFHash(field), url), draft, ranges)


class Paint:
    def __init__(self):
        self.panels = []

    @staticmethod
    def top():
        return K.NSScreen.screens()[0].frame().size.height

    def draw(self, regions):
        for i, (x, y, r, b) in enumerate(regions):
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
                    K.NSColor.colorWithSRGBRed_green_blue_alpha_(0.965, 0.65, 0.14, 0.34)
                )
                self.panels.append(panel)
            panel = self.panels[i]
            panel.setFrame_display_(frame, True)
            panel.orderFrontRegardless()
        for panel in self.panels[len(regions) :]:
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
