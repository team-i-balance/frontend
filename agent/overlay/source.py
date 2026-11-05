"""Map a verified AI composer's code points back to its accessibility text range."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..server.client import MAX_TEXT

SITES = {"chatgpt.com", "claude.ai"}


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
        identifier == "prompt-textarea"
        or "prosemirror" in words
        or "tiptap" in words
        or any(
            s in label
            for s in (
                "message chatgpt",
                "ask chatgpt",
                "reply to claude",
                "chat input",
                "ask anything",
                "send a message",
                "message claude",
                "write your prompt",
                "무엇이든 물어보세요",
                "claude에게",
                "chatgpt에게",
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


# Read only the editable block at the bottom of a recognizable Claude Code / Codex TUI.
PROMPT = re.compile(r"^([ \t]*)([❯›]) ")
RULE = re.compile(r"^\s*[─━╌-]{5,}\s*$")
FOOTER = re.compile(
    r"\?\s*(?:for |to (?:show|see) )?shortcuts|\d+%\s*context (?:left|remaining)|"
    r"(?:accept edits|bypass permissions|plan mode)\s*(?:on|·|\()|shift\+tab to cycle|"
    r"快捷键|단축키",
    re.I,
)
MODEL_FOOTER = re.compile(r"^\s*(?:gpt-[\w.-]+|o[134](?:-[\w.-]+)?)\s.*[·•]", re.I)


def terminal_draft(visible, title=""):
    lines = visible.splitlines(keepends=True)
    # No guess from old prompts in scrollback or ordinary shell input.
    if not lines or len(visible) > 100_000:
        return None
    rows = [line.rstrip("\r\n") for line in lines]
    last = max((i for i, row in enumerate(rows) if row.strip()), default=-1)
    starts = [(i, PROMPT.match(rows[i])) for i in range(max(0, last - 100), last + 1)]
    for i, marker in reversed(starts):
        if marker:
            break
    else:
        return None
    claude = marker[2] == "❯"
    # An ended CLI followed by a shell prompt or output is no longer an editable draft.
    j = i + 1
    while j <= last and not (
        RULE.fullmatch(rows[j]) or FOOTER.search(rows[j]) or MODEL_FOOTER.match(rows[j])
    ):
        if rows[j].strip() and not rows[j].startswith(" " * marker.end()):
            return None
        j += 1
    rule = j <= last and bool(RULE.fullmatch(rows[j]))
    footer = [s.strip() for s in rows[j : last + 1] if s.strip() and not RULE.fullmatch(s)]
    if len(footer) > 3 or any(not (FOOTER.search(s) or MODEL_FOOTER.match(s)) for s in footer):
        return None
    if claude:
        if not rule or i == 0 or not RULE.fullmatch(rows[i - 1]):
            return None
        if not footer and not re.search(r"claude", title, re.I):
            return None
    elif not footer:
        return None
    elif not (
        re.search(r"\bcodex\b", title, re.I)
        or re.search(r"OpenAI Codex\b", visible[:1600], re.I)
        or any(MODEL_FOOTER.match(s) or re.search(r"\d+%\s*context", s, re.I) for s in footer)
    ):
        return None
    starts_at = sum(map(len, lines[:i]))
    width = len(rows[i - 1].rstrip()) if claude else 0
    chunks, runs, length, previous = [], [], 0, ""
    for k in range(i, j):
        value = rows[k][marker.end() :].rstrip()
        if k > i and not (width and len(previous.rstrip()) >= width):
            chunks.append("\n")
            length += 1
        chunks.append(value)
        runs.append((length, length + len(value), starts_at + marker.end()))
        length += len(value)
        starts_at += len(lines[k])
        previous = rows[k]
    text = "".join(chunks).rstrip("\n")
    return Draft(text, tuple(runs)) if len(text) <= MAX_TEXT else None


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


def end_only(draft, start, end):
    """Terminal prompts have no addressable caret: only the last typed value can be retyped with Backspace."""
    return end == len(draft.text) and "\n" not in draft.text[start:end]


OWN_WINDOW = object()
