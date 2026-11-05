"""Map a verified AI composer's code points back to its accessibility text range.

Scope (2026-11-05): only Claude in the browser (claude.ai, including Claude Code on the web).
ChatGPT, terminals and CLIs are not monitored.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit


SITES = {"claude.ai"}


def site(url):
    try:
        parsed = urlsplit(str(url))
        if (
            parsed.scheme == "https"
            and parsed.hostname in SITES
            and parsed.port in (None, 443)
            and not parsed.username
        ):
            return parsed.hostname
    except ValueError:
        pass
    return ""


def composer(identifier, classes, name, multiline):
    words = (classes or "").lower().split()
    label = (name or "").strip().casefold()
    return (
        "prosemirror" in words
        or "tiptap" in words
        or any(
            s in label
            for s in (
                "reply to claude",
                "message claude",
                "write your prompt",
                "chat input",
                "send a message",
                "claude에게",
            )
        )
        or (multiline and not label)
    )


@dataclass(frozen=True)
class Draft:
    text: str
    # (start in draft, end in draft, start in the provider's text), excluding TUI decorations.
    runs: tuple

    @classmethod
    def plain(cls, text):
        return cls(text, ((0, len(text), 0),))

    def parts(self, start, end):
        for a, b, source in self.runs:
            left, right = max(start, a), min(end, b)
            if left < right:
                yield source + left - a, source + right - a


def clip(box, viewport):
    x, y, r, b = box
    left, top, right, bottom = viewport
    clipped = max(x, left), max(y, top), min(r, right), min(b, bottom)
    return clipped if clipped[2] > clipped[0] and clipped[3] > clipped[1] else None


@dataclass
class Snapshot:
    key: tuple
    draft: Draft
    rectangles: (
        object  # Callable(start, end) -> visible screen rectangles; called on the provider thread only.
    )
    # Callable(start, end, text) -> bool: replace draft[start:end] in the real editor as if typed.
    # Provider thread only. The caller re-reads the editor to verify the result.
    replace: object = None
    bounds: tuple = ()  # visible editor rectangle (left, top, right, bottom)
    focused: bool = True  # keyboard focus is in this editor (Enter goes to it)


OWN_WINDOW = object()
