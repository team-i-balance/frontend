"""Map a verified AI composer's code points back to its accessibility text range.

Scope: Claude in the browser (claude.ai, including Claude Code on the web), Codex in the browser
(chatgpt.com/codex) and the Claude Code / Codex CLIs in a terminal. Other ChatGPT pages are not read.
"""

import re
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..server.client import MAX_TEXT

# Web pages that are read: site id -> (host, path prefix). Other pages on the same host are not read.
PAGES = {
    "claude.ai": ("claude.ai", "/"),
    "chatgpt.com/codex": ("chatgpt.com", "/codex"),
}
SITES = set(PAGES)
# Terminal CLIs: snapshot kind -> the prompt marker the CLI draws.
TERMINALS = {"cli:claude": "❯", "cli:codex": "›"}
# The AI each decision goes to (/process/decide target_ai).
TARGETS = {
    "claude.ai": "Claude (claude.ai)",
    "chatgpt.com/codex": "Codex (chatgpt.com/codex)",
    "cli:claude": "Claude Code (CLI)",
    "cli:codex": "Codex (CLI)",
}


def site(url):
    try:
        parsed = urlsplit(str(url))
        if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.username:
            return ""
        path = parsed.path or "/"
        for name, (host, prefix) in PAGES.items():
            if parsed.hostname == host and (prefix == "/" or path == prefix or path.startswith(prefix + "/")):
                return name
    except ValueError:
        pass
    return ""


def composer(identifier, classes, name, multiline):
    words = (classes or "").lower().split()
    label = (name or "").strip().casefold()
    return (
        identifier == "prompt-textarea"  # ChatGPT·Codex composer
        or "prosemirror" in words
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
                "describe a task",
                "ask codex",
                "request changes",
                "codex에게",
            )
        )
        or (multiline and not label)
    )


def sends(shift, ctrl, meta):
    """A Return that may send the prompt: anything but a plain Shift+Return (a new line).

    Many composers also send on Ctrl/Cmd+Return, so that is held back as well."""
    return not shift or bool(ctrl) or bool(meta)


@dataclass(frozen=True)
class Draft:
    text: str
    # (start in draft, end in draft, start in the provider's text), excluding TUI decorations.
    runs: tuple
    app: str = ""      # terminal kind (TERMINALS) when read from a CLI prompt
    rows: tuple = ()   # terminal: first and last row of the prompt in the provider's visible text

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
    if len(text) > MAX_TEXT:
        return None
    # The prompt block for chip placement: the marker row down to the last row with typed text.
    bottom = max((k for k in range(i, j) if rows[k][marker.end() :].strip()), default=i)
    return Draft(text, tuple(runs), "cli:claude" if claude else "cli:codex", (i, bottom))


def row_span(visible, first, last):
    """Code-point offsets of rows first..last (without the line break) in the provider's visible text."""
    lines = visible.splitlines(keepends=True)
    start = sum(map(len, lines[:first]))
    end = sum(map(len, lines[:last])) + len(lines[last].rstrip("\r\n")) if last < len(lines) else len(visible)
    return start, max(start, end)


# ---------------------------------------------------------------- typing into a terminal prompt
# A TUI prompt has no caret that accessibility can address or move. Changes are typed like a person
# would: find the caret with a marker character, erase the prompt's last line back to the first change
# with Backspace and type the rest again.
MARKER = "¤"


@dataclass(frozen=True)
class Keys:
    """Keystrokes into a terminal prompt (provider thread only)."""

    type: object  # type(text) -> bool
    back: object  # back(times) -> bool, one Backspace per character


def single_cells(text):
    """One Backspace removes exactly one of these characters (no astral, combining, joiner or control)."""
    return all(ord(c) < 0x10000 and unicodedata.category(c)[0] not in "MC" for c in text)


def retype_plan(text, changes):
    """All changes must sit on the prompt's last line. Returns (tail now, tail after, prompt after) or None."""
    if not changes:
        return None
    start = min(a for a, _, _ in changes)
    after = text
    for a, b, new in sorted(changes, key=lambda change: change[0], reverse=True):
        after = after[:a] + new + after[b:]
    old, new = text[start:], after[start:]
    if MARKER in text or "\n" in old or not single_cells(old) or any(c < " " for c in new):
        return None
    return old, new, after


def _until(read, test, wait):
    limit = time.monotonic() + wait
    while True:
        now = read()
        if test(now):
            return now
        if time.monotonic() >= limit:
            return None
        time.sleep(0.03)


def caret_spaces(read, keys, text, wait=0.6):
    """Spaces typed after the prompt's text when the caret is at its end. None if the caret is elsewhere.

    The marker lands at the caret; one Backspace removes it again wherever it landed."""
    if not keys.type(MARKER):
        return None
    seen = _until(read, lambda now: now is not None and now != text, wait)
    if seen is None or MARKER not in seen:
        return None  # the prompt did not show the marker (or changed otherwise): touch nothing more
    if not keys.back(1) or _until(read, lambda now: now == text, wait) is None:
        return None
    before, _, after = seen.partition(MARKER)
    spaces = len(before) - len(text)
    if after or spaces < 0 or before != text + " " * spaces:
        return None
    return spaces


def retype(read, keys, text, changes, wait=0.6):
    """Apply changes to a terminal prompt as if typed. Returns the prompt it should show, or None if refused.

    read() returns the prompt's text now (None if it is gone). The caller verifies the result."""
    plan = retype_plan(text, changes)
    if plan is None:
        return None
    old, new, after = plan
    spaces = caret_spaces(read, keys, text, wait)
    if spaces is None:
        return None
    if not keys.back(len(old) + spaces) or not keys.type(new + " " * spaces):
        return None
    return after


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
    bounds: tuple = ()  # visible editor rectangle (left, top, right, bottom); a terminal's prompt block
    focused: bool = True  # keyboard focus is in this editor (Enter goes to it)
    keys: object = None  # Keys for a terminal prompt (no addressable caret): edits go through retype()


OWN_WINDOW = object()
