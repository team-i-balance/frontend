"""macOS pasteboard access. All calls run on the Tk main thread."""
try:
    from AppKit import NSPasteboard, NSPasteboardTypeString, NSWorkspace
except ImportError:
    NSPasteboard = None


def available():
    return NSPasteboard is not None


def clipboard_sequence():
    return int(NSPasteboard.generalPasteboard().changeCount()) if available() else 0


def get_clipboard_text():
    if not available():
        return None
    value = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
    return str(value) if value is not None else None


def set_clipboard_text(text):
    if not available():
        return False
    board = NSPasteboard.generalPasteboard()
    board.clearContents()
    return bool(board.setString_forType_(text, NSPasteboardTypeString))


def own_window_in_foreground():
    if not available():
        return False
    import os
    active = NSWorkspace.sharedWorkspace().frontmostApplication()
    return active is not None and active.processIdentifier() == os.getpid()
