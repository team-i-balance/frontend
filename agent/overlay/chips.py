"""Cascading chips over a highlighted word. Their own look, drawn with Pillow (not the app theme).

A chip lists its options top to bottom. Picking an option that has more options opens the next chip
beside it and keeps the earlier chips on screen with the chosen row lit. Picking a final option plays
the closing animation, removes every chip and then runs the option. Chips sit away from the prompt so
its text stays readable; dashed ERD-style lines tie the word to the first chip and each chosen row to
the chip it opened.

Everything here is plain geometry and images. A surface (a Win32 layered window, or a Tk window
elsewhere) only shows the finished frame, so the layout and animation are testable on any platform.
"""

from __future__ import annotations

import math
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- look (independent of gui.theme)
INK = (15, 17, 24)
EDGE = (46, 51, 68)
TEXT = (236, 238, 245)
DIM = (140, 148, 168)
VIOLET = (124, 108, 255)
SKY = (56, 189, 248)
LINK = (160, 146, 255)
HOVER = (29, 32, 45)
CHOSEN = (42, 36, 88)
FLASH = (88, 74, 178)
BADGE = (31, 34, 48)

# Sizes at 96 DPI.
HEAD, ROW, PAD, RADIUS = 42, 36, 6, 12
MIN_W, MAX_W = 212, 340
GAP = 56          # prompt box (or word) to the first chip
HGAP = 68         # chosen row to the next chip
SHADOW = 26       # margin kept around a card for its shadow
EDGE_MARGIN = 10  # keep chips this far inside the monitor's work area

OPEN_S, CLOSE_S, LINE_S, STAGGER_S = 0.26, 0.2, 0.24, 0.045


def mix(a, b, t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def ease(t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return 1 - (1 - t) ** 3


def approach(value, target, dt, tau):
    return target if abs(target - value) < 0.004 else value + (target - value) * (1 - math.exp(-dt / tau))


@dataclass
class Option:
    label: str
    action: Callable | None = None
    menu: "Menu | None" = None


@dataclass
class Menu:
    title: str
    options: list
    note: str = ""


# ---------------------------------------------------------------- fonts
def _font_files():
    if sys.platform == "win32":
        d = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        return {
            "regular": [os.path.join(d, "malgun.ttf")],
            "bold": [os.path.join(d, "malgunbd.ttf"), os.path.join(d, "malgun.ttf")],
            "mono": [os.path.join(d, n) for n in ("CascadiaMono.ttf", "consola.ttf", "malgun.ttf")],
        }
    if sys.platform == "darwin":
        sd = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
        return {"regular": [sd], "bold": [sd], "mono": ["/System/Library/Fonts/Menlo.ttc", sd]}
    n = "/usr/share/fonts/truetype/nanum/"
    return {
        "regular": [n + "NanumGothic.ttf", n + "NanumBarunGothic.ttf"],
        "bold": [n + "NanumGothicBold.ttf", n + "NanumSquareB.ttf", n + "NanumGothic.ttf"],
        "mono": [n + "NanumGothicCoding.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
    }


_FONTS: dict = {}


def font(kind, size):
    key = kind, size
    if key not in _FONTS:
        for path in _font_files()[kind]:
            if os.path.exists(path):
                try:
                    _FONTS[key] = ImageFont.truetype(path, size)
                    break
                except OSError:
                    continue
        else:
            _FONTS[key] = ImageFont.load_default(size)
    return _FONTS[key]


def fit(text, face, width):
    if face.getlength(text) <= width:
        return text
    while text and face.getlength(text + "…") > width:
        text = text[:-1]
    return text + "…"


def rounded_mask(size, radius, scale=4):
    w, h = size
    big = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * scale - 1, h * scale - 1), radius * scale, fill=255)
    return big.resize((w, h), Image.LANCZOS)


# ---------------------------------------------------------------- one chip
class Card:
    def __init__(self, menu: Menu, s: float, depth: int, born: float):
        self.menu, self.s, self.depth, self.born = menu, s, depth, born
        self.dying = None
        self.selected = None
        n = len(menu.options)
        self.hover = [0.0] * n
        self.flash = [0.0] * n
        self.close_hover = 0.0
        self.line = []          # connector points into this card (screen coordinates)
        self.line_end = "up"    # direction the connector travels when it reaches the card
        self.x = self.y = None  # animated position
        self.tx = self.ty = 0
        S = self.px
        self.f_title = font("bold", S(13))
        self.f_row = font("regular", S(13))
        self.f_mono = font("mono", S(11))
        self.f_badge = font("mono", S(10))
        widest = max((self.f_row.getlength(o.label) for o in menu.options), default=0)
        title = self.f_title.getlength(menu.title) + (self.f_mono.getlength(menu.note) + S(22) if menu.note else 0)
        inner = max(widest + S(PAD * 2 + 12 + 34 + 34), title + S(30) + (S(26) if depth == 0 else 0))
        self.w = int(max(S(MIN_W), min(S(MAX_W), inner)))
        self.head, self.row, self.pad = S(HEAD), S(ROW), S(PAD)
        self.h = self.head + self.pad * 2 + self.row * n
        self.margin = S(SHADOW)
        self.base = self._base()

    def px(self, v):
        return max(1, round(v * self.s))

    @property
    def rect(self):
        return self.x, self.y, self.x + self.w, self.y + self.h

    def row_box(self, i, x=None, y=None):
        x = self.tx if x is None else x
        y = self.ty if y is None else y
        top = y + self.head + self.pad + self.row * i
        return x + self.pad, top, x + self.w - self.pad, top + self.row

    def close_box(self, x=None, y=None):
        x = self.tx if x is None else x
        y = self.ty if y is None else y
        c, r = x + self.w - self.px(22), self.px(11)
        return c - r, y + self.head // 2 - r, c + r, y + self.head // 2 + r

    def _gradient(self, w, h):
        line = Image.new("RGB", (w, 1))
        for x in range(w):
            line.putpixel((x, 0), mix(VIOLET, SKY, x / max(1, w - 1)))
        return line.resize((w, h))

    def _base(self):
        """Shadow, body, border and header with title: drawn once, anti-aliased."""
        w, h, m, S = self.w, self.h, self.margin, self.px
        img = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
        mask = rounded_mask((w, h), S(RADIUS))
        shadow = Image.new("L", img.size, 0)
        shadow.paste(mask.point(lambda v: v * 120 // 255), (m, m + S(6)))
        shadow = shadow.filter(ImageFilter.GaussianBlur(S(9)))
        img.paste((0, 0, 0, 255), (0, 0), shadow)
        img.putalpha(shadow)
        body = Image.new("RGBA", (w, h), EDGE + (255,))
        inner = rounded_mask((w - 2, h - 2), S(RADIUS) - 1)
        body.paste(INK + (255,), (1, 1), inner)
        head = self._gradient(w - 2, self.head - 1).convert("RGBA")
        clip = Image.new("L", (w - 2, self.head - 1), 0)
        clip.paste(inner.crop((0, 0, w - 2, self.head - 1)), (0, 0))
        body.paste(head, (1, 1), clip)
        body.putalpha(mask)
        img.alpha_composite(body, (m, m))
        d = ImageDraw.Draw(img)
        tx, ty = m + S(14), m + self.head // 2
        title = fit(self.menu.title, self.f_title, w - S(40) - (S(26) if self.depth == 0 else 0))
        d.text((tx, ty), title, font=self.f_title, fill=(255, 255, 255, 255), anchor="lm")
        if self.menu.note:
            face = self.f_mono if self.menu.note.isascii() else self.f_row
            nx = tx + self.f_title.getlength(title) + S(10)
            room = m + w - S(14) - (S(30) if self.depth == 0 else 0) - nx - S(16)
            note = fit(self.menu.note, face, room)
            if room > S(30):
                pill = Image.new("RGBA", (int(face.getlength(note) + S(16)), S(20)), (0, 0, 0, 0))
                ImageDraw.Draw(pill).rounded_rectangle(
                    (0, 0, pill.width - 1, pill.height - 1), S(10), fill=(10, 12, 30, 90)
                )
                img.alpha_composite(pill, (int(nx), ty - pill.height // 2))
                d.text((nx + S(8), ty), note, font=face, fill=(255, 255, 255, 235), anchor="lm")
        return img

    def opacity(self, now):
        shown = ease((now - self.born - 0.08) / OPEN_S)
        if self.dying is not None:
            shown *= 1 - ease((now - self.dying) / CLOSE_S)
        return shown

    def done(self, now):
        return self.dying is not None and now - self.dying >= CLOSE_S

    def render(self, now):
        """The card at its own size plus shadow margin."""
        img = self.base.copy()
        d = ImageDraw.Draw(img)
        S, m = self.px, self.margin
        opened = (now - self.born - 0.08) / OPEN_S
        for i, option in enumerate(self.menu.options):
            appear = ease((opened - i * STAGGER_S / OPEN_S) * 1.6)
            x0, y0, x1, y1 = self.row_box(i, m, m)
            slide = round((1 - appear) * S(10))
            chosen = 1.0 if self.selected == i else 0.0
            lit = max(self.hover[i], chosen)
            fill = mix(INK, HOVER, self.hover[i])
            fill = mix(fill, CHOSEN, chosen)
            fill = mix(fill, FLASH, self.flash[i])
            fill = mix(INK, fill, appear)
            if fill != INK:
                d.rounded_rectangle((x0, y0 + 1, x1 - 1, y1 - 2), S(8), fill=fill + (255,))
            if lit > 0.02:
                bar = max(1, round(S(3) * lit))
                d.rounded_rectangle(
                    (x0 + S(2), y0 + S(9), x0 + S(2) + bar, y1 - S(10)), S(2), fill=mix(INK, VIOLET, lit * appear) + (255,)
                )
            bx = x0 + S(12) + slide
            cy = (y0 + y1) // 2
            badge = mix(BADGE, VIOLET, chosen)
            d.rounded_rectangle((bx, cy - S(9), bx + S(24), cy + S(9)), S(5), fill=mix(INK, badge, appear) + (255,))
            d.text(
                (bx + S(12), cy),
                f"{i + 1:02d}",
                font=self.f_badge,
                fill=mix(INK, (255, 255, 255) if chosen else DIM, appear) + (255,),
                anchor="mm",
            )
            label = fit(option.label, self.f_row, x1 - bx - S(34 + 30))
            color = mix(mix(TEXT, (255, 255, 255), lit), INK, 1 - appear)
            d.text((bx + S(34), cy), label, font=self.f_row, fill=color + (255,), anchor="lm")
            gx = x1 - S(16) + round(S(3) * self.hover[i]) + slide
            glyph = mix(INK, mix(DIM, LINK, lit), appear) + (255,)
            if option.menu is not None:  # opens another chip
                d.line([(gx - S(3), cy - S(5)), (gx + S(2), cy), (gx - S(3), cy + S(5))], fill=glyph, width=S(2), joint="curve")
            else:  # final choice
                d.line([(gx + S(3), cy - S(5)), (gx + S(3), cy + S(1)), (gx - S(6), cy + S(1))], fill=glyph, width=S(2), joint="curve")
                d.line([(gx - S(3), cy - S(2)), (gx - S(6), cy + S(1)), (gx - S(3), cy + S(4))], fill=glyph, width=S(2), joint="curve")
        if self.depth == 0:
            x0, y0, x1, y1 = self.close_box(m, m)
            if self.close_hover > 0.02:
                ring = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
                ImageDraw.Draw(ring).ellipse(
                    (0, 0, ring.width - 1, ring.height - 1), fill=(255, 255, 255, round(60 * self.close_hover))
                )
                img.alpha_composite(ring, (x0, y0))
            c, r = (x0 + x1) // 2, S(4)
            cy = (y0 + y1) // 2
            for a, b in (((c - r, cy - r), (c + r, cy + r)), ((c - r, cy + r), (c + r, cy - r))):
                d.line([a, b], fill=(255, 255, 255, 230), width=S(2))
        return img


# ---------------------------------------------------------------- the whole cascade
class Cascade:
    def __init__(self, menu, anchor, area, screen, scale, surface=None, now=None):
        self.s = scale
        self.anchor = tuple(anchor)
        self.area = tuple(area) if area else None
        self.screen = tuple(screen)
        self.surface = surface
        self.opened = time.monotonic() if now is None else now
        self.clock = self.opened
        self.cards: list[Card] = []
        self.leaving: list[Card] = []
        self.press = None
        self.pointer = (-10_000, -10_000)
        self.closing = None
        self.pending = None
        self.finished = False
        self.side = "above"
        self._push(menu, self.opened)

    def px(self, v):
        return max(1, round(v * self.s))

    # ------------------------------------------------------------ layout
    def _push(self, menu, now):
        card = Card(menu, self.s, len(self.cards), now)
        self.cards.append(card)
        self._layout()
        card.x, card.y = card.tx, card.ty

    def _layout(self):
        S = self.px
        left, top, right, bottom = self.screen
        lo, hi = top + S(EDGE_MARGIN), bottom - S(EDGE_MARGIN)
        ax0, ay0, ax1, ay1 = self.anchor
        box = self.area or self.anchor
        root = self.cards[0]
        above = box[1] - S(GAP) - root.h
        below = box[3] + S(GAP)
        if above >= lo:
            self.side, root.ty = "above", above
        elif below + root.h <= hi:
            self.side, root.ty = "below", below
        else:
            self.side = "below"
            root.ty = max(lo, min(ay1 + S(GAP), hi - root.h))
        root.tx = (ax0 + ax1) // 2 - S(52)
        for parent, card in zip(self.cards, self.cards[1:]):
            _, r0, _, r1 = parent.row_box(parent.selected)
            card.tx = parent.tx + parent.w + S(HGAP)
            card.ty = (r0 + r1) // 2 - card.head // 2
            if self.side == "above":
                card.ty = min(card.ty, box[1] - S(12) - card.h)
            else:
                card.ty = max(card.ty, box[3] + S(12))
            card.ty = max(lo, min(card.ty, hi - card.h))
        far = max(c.tx + c.w for c in self.cards)
        shift = max(0, far - (right - S(EDGE_MARGIN)))
        shift = min(shift, max(0, min(c.tx for c in self.cards) - (left + S(EDGE_MARGIN))))
        low = min(c.tx for c in self.cards) - shift
        if low < left + S(EDGE_MARGIN):
            shift -= left + S(EDGE_MARGIN) - low
        for c in self.cards:
            c.tx -= shift

    def _wire(self, card, parent):
        """Orthogonal connector into card, ERD style."""
        S = self.px
        x, y = card.x, card.y
        if parent is None:
            ax0, ay0, ax1, ay1 = self.anchor
            kx = (ax0 + ax1) // 2
            cx = max(x + S(28), min(kx, x + card.w - S(28)))
            if self.side == "above":
                start, end = (kx, ay0 - S(6)), (cx, y + card.h)
                card.line_end = "up"
            else:
                start, end = (kx, ay1 + S(6)), (cx, y)
                card.line_end = "down"
            mid = (start[1] + end[1]) // 2
            card.line = [start, (kx, mid), (cx, mid), end] if kx != cx else [start, end]
        else:
            _, r0, _, r1 = parent.row_box(parent.selected, parent.x, parent.y)
            start = (parent.x + parent.w - S(2), (r0 + r1) // 2)
            end = (x, y + card.head // 2)
            card.line_end = "right"
            mid = (start[0] + end[0]) // 2
            card.line = [start, (mid, start[1]), (mid, end[1]), end] if start[1] != end[1] else [start, end]

    # ------------------------------------------------------------ input
    def _hit(self, x, y):
        if self.closing is not None:
            return None
        for ci, card in enumerate(self.cards):
            if card.depth == 0 and _inside(card.close_box(), x, y):
                return ("close",)
            for ri in range(len(card.menu.options)):
                if _inside(card.row_box(ri), x, y):
                    return ("row", ci, ri)
        return None

    def contains(self, x, y):
        return self.closing is None and any(_inside((c.tx, c.ty, c.tx + c.w, c.ty + c.h), x, y) for c in self.cards)

    def move(self, x, y):
        self.pointer = (x, y)

    def press_at(self, x, y):
        self.press = self._hit(x, y)
        if self.press and self.press[0] == "row":
            self.cards[self.press[1]].flash[self.press[2]] = 0.7

    def release_at(self, x, y):
        hit, pressed, self.press = self._hit(x, y), self.press, None
        if hit is None or hit != pressed:
            return
        if hit[0] == "close":
            self.close()
        else:
            self.choose(hit[1], hit[2])

    def click(self, x, y):
        """Press and release at one point."""
        self.press_at(x, y)
        self.release_at(x, y)

    def choose(self, ci, ri, now=None):
        now = self.clock if now is None else now
        card = self.cards[ci]
        option = card.menu.options[ri]
        card.flash[ri] = 1.0
        if option.menu is not None and card.selected == ri and len(self.cards) > ci + 1:
            return
        for gone in self.cards[ci + 1 :]:
            gone.dying = now
            self.leaving.append(gone)
        del self.cards[ci + 1 :]
        card.selected = ri
        if option.menu is not None:
            self._push(option.menu, now)
        else:
            self.finish(option.action, now)

    def finish(self, action=None, now=None):
        if self.closing is None:
            self.closing = self.clock if now is None else now
            self.pending = action
            for card in self.cards:
                card.dying = self.closing

    def close(self, now=None):
        self.finish(None, now)

    # ------------------------------------------------------------ animation
    def step(self, now=None):
        """Advance the animation. Returns the action to run once the closing animation ended."""
        now = time.monotonic() if now is None else now
        dt = max(0.0, min(0.1, now - self.clock))
        self.clock = now
        hit = self._hit(*self.pointer)
        for ci, card in enumerate(self.cards + self.leaving):
            if card.x is None:
                card.x, card.y = card.tx, card.ty
            card.x = round(approach(card.x, card.tx, dt, 0.07))
            card.y = round(approach(card.y, card.ty, dt, 0.07))
            live = ci < len(self.cards)
            for ri in range(len(card.hover)):
                over = live and hit == ("row", ci, ri)
                card.hover[ri] = approach(card.hover[ri], 1.0 if over else 0.0, dt, 0.05)
                card.flash[ri] = approach(card.flash[ri], 0.0, dt, 0.12)
            card.close_hover = approach(card.close_hover, 1.0 if live and hit == ("close",) else 0.0, dt, 0.05)
        for parent, card in zip([None] + self.cards, self.cards):
            self._wire(card, parent)
        self.leaving = [c for c in self.leaving if not c.done(now)]
        if self.closing is not None and now - self.closing >= CLOSE_S + 0.04 and not self.finished:
            self.finished = True
            action, self.pending = self.pending, None
            return action or _noop
        return None

    def alpha(self, now):
        if self.closing is None:
            return 255
        return round(255 * (1 - ease((now - self.closing) / CLOSE_S)))

    def render(self, now=None):
        """Return (image, left, top) for the current frame."""
        now = self.clock if now is None else now
        S = self.px
        cards = [c for c in self.leaving] + self.cards
        boxes = [self._pad(self.anchor, S(18))]
        for card in cards:
            m = card.margin
            boxes.append((card.x - m, card.y - m, card.x + card.w + m, card.y + card.h + m))
            for x, y in card.line:
                boxes.append((x - S(10), y - S(10), x + S(10), y + S(10)))
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[2] for b in boxes)
        bottom = max(b[3] for b in boxes)
        frame = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 0))
        vec = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(vec)
        # The word: a ring, and a ripple when the chip opens.
        ring = self._pad(self.anchor, S(4))
        d.rounded_rectangle(self._shift(ring, left, top), S(6), outline=LINK + (255,), width=S(2))
        ripple = (now - self.opened) / 0.7
        if 0 <= ripple < 1:
            p = ease(ripple)
            d.rounded_rectangle(
                self._shift(self._pad(self.anchor, S(4) + round(S(12) * p)), left, top),
                S(8),
                outline=LINK + (round(200 * (1 - p)),),
                width=S(2),
            )
        phase = (now - self.opened) * S(26)
        for card in cards:
            grow = ease((now - card.born) / LINE_S)
            if card.dying is not None:
                grow *= 1 - ease((now - card.dying) / CLOSE_S)
            points = [(x - left, y - top) for x, y in card.line]
            self._dashes(d, points, grow, phase, S)
            if grow > 0.98 and points:
                self._end(d, points[-1], card.line_end, S)
            if grow > 0.02 and points:
                x, y = points[0]
                d.ellipse((x - S(4), y - S(4), x + S(4), y + S(4)), fill=LINK + (255,), outline=INK + (255,), width=1)
        frame.alpha_composite(vec)
        for card in cards:
            op = card.opacity(now)
            if op <= 0.01:
                continue
            img = card.render(now)
            if op < 0.999:
                img.putalpha(img.getchannel("A").point(lambda v, k=op: round(v * k)))
            lift = round((1 - op) * S(10)) * (1 if self.side == "above" else -1)
            frame.alpha_composite(img, (card.x - card.margin - left, card.y - card.margin - top + lift))
        return frame, left, top

    def present(self, now=None):
        if self.surface is not None:
            image, x, y = self.render(now)
            self.surface.present(image, x, y, self.alpha(self.clock if now is None else now))

    @staticmethod
    def _pad(box, n):
        return box[0] - n, box[1] - n, box[2] + n, box[3] + n

    @staticmethod
    def _shift(box, dx, dy):
        return box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy

    @staticmethod
    def _dashes(d, points, grow, phase, S):
        if len(points) < 2 or grow <= 0:
            return
        lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        limit = sum(lengths) * grow
        on, off = S(7), S(5)
        period = on + off
        walked = 0.0
        for (a, b), length in zip(zip(points, points[1:]), lengths):
            if length == 0:
                continue
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            pos = 0.0
            while pos < length:
                along = walked + pos
                if along >= limit:
                    return
                cycle = (along - phase) % period
                if cycle < on:
                    stop = min(length, pos + on - cycle, limit - walked)
                    d.line(
                        [(a[0] + ux * pos, a[1] + uy * pos), (a[0] + ux * stop, a[1] + uy * stop)],
                        fill=LINK + (255,),
                        width=S(2),
                    )
                    pos = stop
                else:
                    pos += period - cycle
            walked += length

    @staticmethod
    def _end(d, point, direction, S):
        """Crow's foot where the line meets a chip (ERD 'many' end)."""
        x, y = point
        n, w = S(9), S(6)
        if direction == "right":
            back = x - n
            lines = [((back, y), (x, y - w)), ((back, y), (x, y)), ((back, y), (x, y + w)), ((back - S(4), y - w), (back - S(4), y + w))]
        else:
            sign = 1 if direction == "up" else -1
            back = y + n * sign
            bar = back + S(4) * sign
            lines = [((x, back), (x - w, y)), ((x, back), (x, y)), ((x, back), (x + w, y)), ((x - w, bar), (x + w, bar))]
        for a, b in lines:
            d.line([a, b], fill=LINK + (255,), width=S(2))


def _inside(box, x, y):
    return box[0] <= x < box[2] and box[1] <= y < box[3]


def _noop():
    return None


# ---------------------------------------------------------------- Tk surface (macOS, development)
class TkSurface:
    """Shows frames in a Tk window. Transparent on macOS; on a dark backdrop elsewhere."""

    def __init__(self, root):
        import tkinter as tk

        self.mac = sys.platform == "darwin"
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        bg = "systemTransparent" if self.mac else "#202124"
        if self.mac:
            self.win.attributes("-transparent", True)
        self.win.configure(bg=bg)
        self.label = tk.Label(self.win, bd=0, highlightthickness=0, bg=bg)
        self.label.pack()
        self.photo = None

    def present(self, image, x, y, alpha=255):
        from PIL import ImageTk

        if not self.mac:
            back = Image.new("RGBA", image.size, (32, 33, 36, 255))
            back.alpha_composite(image)
            image = back
        self.photo = ImageTk.PhotoImage(image)
        self.label.configure(image=self.photo)
        self.win.geometry(f"{image.width}x{image.height}+{x}+{y}")
        try:
            self.win.attributes("-alpha", alpha / 255)
        except Exception:
            pass
        self.win.deiconify()
        self.win.lift()

    def close(self):
        self.win.destroy()
