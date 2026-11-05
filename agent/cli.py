"""Guarded terminal composer for Claude Code and Codex CLI.

칩 흐름은 브라우저·원래 입력창과 같다 (새 창 없이 칩을 펼쳐 마무리):
  하이라이트에서 F2·클릭, 또는 하이라이트가 남은 채 Enter → 마스킹 / 원문유지
    마스킹 → 바뀔 값만 보여 주고 적용 / 모두 적용 → 입력 중인 글의 그 자리만 바꾼다
    원문유지 → 업무상 필요 / 오탐 / 승인된 개인정보
  칩이 열려 있으면 숫자·←→·Tab으로 고르고 Enter로 누른다. Esc는 뒤로·닫기.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style

from .browser.actions import KEEP_REASONS, ChipError
from .browser.peer import Peer
from .overlay.live import stable_ranges
from .sanitize.josa import ro

TITLE = "AI 누리 보안"
# 디자인 설정의 색 (docs/DESIGN-SETTINGS.md). 하이라이트는 옅은 파란 면 + 밑줄, 칩은 요소 면, 고른 칩은 선택 면
STYLE = Style.from_dict(
    {
        "hit": "bg:#2a3650 #f1f3f5 underline",
        "head": "#a3abb4",
        "title": "#a3abb4",
        "chip": "bg:#343a40 #f1f3f5",
        "chip.focus": "bg:#2a3650 #8fb5ff underline",
        "chip.off": "bg:#343a40 #868e96",
        "word": "bg:#3b4148 #f1f3f5",
        "word.deleted": "#a3abb4 strike",
        "success": "#55dc92",
        "warning": "#f8c952",
        "error": "#ff7272",
        "info": "#62caf4",
        "note": "#c1c7ce",
    }
)
ICON = {"success": "✓", "warning": "!", "error": "×", "info": "i"}


class Highlights(Processor):
    def __init__(self, editor):
        self.editor = editor

    def apply_transformation(self, ti):
        line = ti.document.lines[ti.lineno]
        start = ti.document.translate_row_col_to_index(ti.lineno, 0)
        pos, chunks = 0, []
        for span in self.editor.spans:
            left, right = max(pos, span["start"] - start, 0), min(len(line), span["end"] - start)
            if left < right:
                chunks.extend([("", line[pos:left]), ("class:hit", line[left:right])])
                pos = right
        chunks.append(("", line[pos:]))
        return Transformation(chunks)


class PromptControl(BufferControl):
    def __init__(self, editor):
        self.editor = editor
        super().__init__(buffer=editor.buffer, input_processors=[Highlights(editor)], focus_on_click=True)

    def mouse_handler(self, event):
        result = super().mouse_handler(event)
        if event.event_type == MouseEventType.MOUSE_UP:
            self.editor.open_at_cursor(fallback=False)
        return result


class Composer:
    def __init__(self, peer, provider, last_reply=""):
        self.peer, self.provider = peer, provider
        self.last_reply = last_reply
        self.spans, self.text, self.revision, self.checked = [], "", 0, -1
        self.chip = None
        self.serial = 0
        self.busy = self.working = False
        self.note = ""
        self.buffer = Buffer(multiline=True, on_text_changed=self.change)
        control = PromptControl(self)
        is_open = Condition(lambda: self.chip is not None)
        kb = KeyBindings()
        kb.add("c-c")(lambda e: self.app.exit(result=None))
        kb.add("c-j")(lambda e: self.buffer.insert_text("\n"))
        kb.add("escape")(lambda e: self.back())
        kb.add("f2")(lambda e: self.open_at_cursor(fallback=True))
        kb.add("f4")(lambda e: self.start(self.restore))
        kb.add("c-m")(lambda e: self.enter())
        for key, step in (("left", -1), ("right", 1), ("tab", 1), ("s-tab", -1)):
            kb.add(key, filter=is_open)(lambda e, step=step: self.move(step))
        for n in range(1, 10):
            kb.add(str(n), filter=is_open)(lambda e, i=n - 1: self.press(i))
        self.app = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(
                            FormattedTextControl(
                                [("class:head", f"{TITLE} · {provider}   Enter 전송 · Ctrl+J 줄바꿈 · "
                                                "F2 칩 · F4 답변 복원 · Ctrl+C 종료")]
                            ),
                            height=1,
                        ),
                        Window(control, wrap_lines=True, height=5),
                        Window(FormattedTextControl(self.render), wrap_lines=True),
                        Window(FormattedTextControl(lambda: [("class:note", self.note)]), wrap_lines=True),
                    ]
                ),
                focused_element=control,
            ),
            key_bindings=kb,
            full_screen=False,
            mouse_support=True,
            style=STYLE,
        )

    # ------------------------------------------------------------------ 검사
    def change(self, _):
        new = self.buffer.text
        self.spans = stable_ranges(self.spans, self.text, new)
        self.text, self.revision = new, self.revision + 1
        if self.chip is not None and not self.chip.get("done"):
            self.chip = None  # 자리가 바뀌었다 (끝 단계 칩은 남긴다)
        self.note = "검사 중…" if new.strip() else ""
        self.scan_soon()

    def scan_soon(self):
        if not self.busy:
            self.busy = True
            self.app.create_background_task(self.scan())

    async def scan(self):
        try:
            while True:
                rev, text = self.revision, self.text
                try:
                    data = (
                        await asyncio.to_thread(self.peer, "preview", text=text)
                        if text.strip()
                        else {"spans": [], "coverage": ""}
                    )
                    self.spans = stable_ranges(data["spans"], text, self.text)
                    if self.revision == rev:
                        self.checked = rev
                        self.note = (
                            (data["coverage"] + (f" · 표시 {len(self.spans)}곳" if self.spans else ""))
                            if text
                            else ""
                        )
                except ChipError as exc:
                    if rev == self.revision:
                        self.checked = -1
                        self.spans = []
                        self.note = str(exc)
                self.app.invalidate()
                if rev == self.revision:
                    break
        finally:
            self.busy = False

    def enter(self):
        if self.chip is not None:
            self.press(self.chip["focus"])
            return
        if self.working or not self.text.strip():
            return
        if self.checked != self.revision:
            self.note = "검사가 끝나면 다시 Enter를 눌러 주세요."
            self.scan_soon()
            return
        if self.spans:
            # 하이라이트가 남아 있으면 보내지 않고 첫 항목의 칩을 연다
            self.open(self.spans[0], "보내기 전에 표시된 항목을 처리해 주세요")
            return
        self.app.exit(result=self.text)

    # ------------------------------------------------------------------ 칩
    def open_at_cursor(self, fallback):
        span = next((s for s in self.spans if s["start"] <= self.buffer.cursor_position < s["end"]), None)
        if span is None and fallback and self.spans:
            span = self.spans[0]
        if span is not None:
            self.open(span)
        elif fallback:
            self.note = "표시된 항목이 없어요."
            self.app.invalidate()

    def open(self, span, note=""):
        self.serial += 1
        self.chip = {"span": span, "text": self.text, "id": self.serial}
        self.root(note)

    def show(self, chip, *, title="", note="", word=None, deleted=False, message="", tone="success", chips=(),
             back=None):
        if self.chip is not chip:
            return
        chip.update(title=title, note=note, word=word, deleted=deleted, message=message, tone=tone,
                    chips=list(chips), back=back, focus=0)
        enabled = [i for i, (_, _, run) in enumerate(chip["chips"]) if run]
        chip["focus"] = enabled[0] if enabled else 0
        self.app.invalidate()

    def root(self, note=""):
        chip = self.chip
        span = chip["span"]
        keep = span.get("keep", True) is not False
        self.show(chip, title=span["label"], note=note, tone="warning",
                  message="" if keep else "비밀값·기밀은 원문으로 보낼 수 없어요.",
                  chips=[("mask", "마스킹", lambda: self.start(lambda: self.mask(chip, False))),
                         ("keep", "원문유지", (lambda: self.reasons(chip)) if keep else None)])

    def back(self):
        chip = self.chip
        if chip is None:
            return
        if chip.get("back") and not chip.get("done"):
            chip["back"]()
        else:
            self.chip = None
            self.app.invalidate()

    def move(self, step):
        chip = self.chip
        enabled = [i for i, (_, _, run) in enumerate(chip["chips"]) if run]
        if enabled:
            at = enabled.index(chip["focus"]) if chip["focus"] in enabled else 0
            chip["focus"] = enabled[(at + step) % len(enabled)]
            self.app.invalidate()

    def press(self, index):
        chip = self.chip
        if chip is None or not 0 <= index < len(chip["chips"]):
            return
        _, _, run = chip["chips"][index]
        if run:
            chip["focus"] = index
            run()

    def start(self, job):
        if not self.working:
            self.working = True
            self.app.create_background_task(self._run(job))

    async def _run(self, job):
        chip = self.chip
        try:
            await job()
        except ChipError as exc:
            if chip is not None and self.chip is chip:
                self.show(chip, title=chip.get("title", ""), message=str(exc), tone="error", back=chip.get("back"),
                          chips=[("back", "뒤로", lambda: self.root())] if not chip.get("done") else [])
            else:
                self.note = str(exc)
        finally:
            self.working = False
            self.app.invalidate()

    async def call(self, name, **kw):
        return await asyncio.to_thread(self.peer, name, **kw)

    async def mask(self, chip, every):
        span = chip["span"]
        title = span["label"] + " · " + ("모두 마스킹" if every else "마스킹")
        self.show(chip, title=title, word="바꿀 값을 만드는 중…", chips=[("apply", "적용", None)], back=self.root)
        out = await self.call("mask", text=chip["text"], start=span["start"], end=span["end"], label=span["label"],
                              every=every)
        others = len({s["start"] for s in self.spans})
        chips = [("apply", "적용" + (f" · {out['count']}곳" if out["count"] > 1 else ""),
                  lambda: self.start(lambda: self.apply(chip, out)))]
        if not every and others > 1:
            chips.append(("all", f"모두 적용 {others}", lambda: self.start(lambda: self.mask(chip, True))))
        self.show(chip, title=title, word=out["preview"] if out["deleted"] else out["word"], deleted=out["deleted"],
                  chips=chips, back=self.root)

    def edit(self, edits):
        """입력 중인 글의 그 자리만 바꾼다 (뒤에서부터, 입력하듯이: 되돌리기 기록에 남는다)."""
        for a, b, new in sorted(edits, key=lambda e: e[0], reverse=True):
            self.buffer.cursor_position = a
            self.buffer.delete(b - a)
            self.buffer.insert_text(new)

    async def apply(self, chip, out):
        if self.text != chip["text"]:
            raise ChipError("입력이 바뀌었어요. F2로 칩을 다시 열어 주세요.")
        await self.call("commit", ticket=out["ticket"], keys=out["keys"])
        chip["done"] = True
        self.edit(out["edits"])
        message = "지웠어요" if out["deleted"] else f"{out['word']}{ro(out['word'])} 바꿨어요"
        self.finish(chip, message, lambda: self.start(lambda: self.undo_mask(chip, out)))

    def reasons(self, chip):
        span = chip["span"]
        self.show(chip, title=span["label"] + " · 원문유지 사유", back=self.root,
                  chips=[(code, name, lambda code=code: self.start(lambda: self.keep(chip, code)))
                         for code, name in KEEP_REASONS.items()])

    async def keep(self, chip, reason):
        span = chip["span"]
        if self.text != chip["text"]:
            raise ChipError("입력이 바뀌었어요. F2로 칩을 다시 열어 주세요.")
        out = await self.call("keep", text=chip["text"], start=span["start"], end=span["end"], reason=reason)
        value = chip["text"][span["start"]:span["end"]]
        self.spans = [s for s in self.spans if self.text[s["start"]:s["end"]] != value]
        chip["done"] = True

        async def undo():
            await self.call("unkeep", text=chip["text"], start=span["start"], end=span["end"])
            self.chip = None
            self.checked = -1
            self.revision += 1
            self.scan_soon()

        self.finish(chip, f"원문을 유지해요 · {out['reason']}", lambda: self.start(undo))

    def finish(self, chip, message, undo):
        chips = []
        if self.spans:
            chips.append(("next", "다음 항목", lambda: self.open(self.spans[0])))
        chips.append(("undo", "되돌리기", undo))
        self.show(chip, title=chip["span"]["label"], message=message, tone="success", chips=chips)

    async def undo_mask(self, chip, out):
        back = await self.call("undo", ticket=out["ticket"], keys=out["keys"], text=self.text)
        await self.call("commit", ticket=out["ticket"], keys=out["keys"], applied=False)
        self.edit(back["edits"])
        self.chip = None

    async def restore(self):
        """최근 답변의 바꾼 값을 원래 값으로 (서버는 한 번만). 결과는 아래 줄에 보여 준다."""
        if not self.last_reply:
            raise ChipError("복원할 답변이 없어요. 마스킹해 보낸 뒤 받은 답변만 복원할 수 있어요.")
        data = await self.call("restore", text=self.last_reply)
        self.note = "복원한 답변\n" + data["text"]

    def render(self):
        chip = self.chip
        if chip is None or "chips" not in chip:
            return []
        out = []
        if chip.get("title"):
            out += [("class:title", ("‹ " if chip.get("back") and not chip.get("done") else "") + chip["title"]),
                    ("", "\n")]
        if chip.get("note"):
            out += [("class:warning", f"{ICON['warning']} {chip['note']}"), ("", "\n")]
        if chip.get("word") is not None:
            out += [("class:word.deleted" if chip.get("deleted") else "class:word", f" {chip['word']} "), ("", "\n")]
        if chip.get("message"):
            tone = chip.get("tone", "success")
            out += [(f"class:{tone}", f"{ICON.get(tone, 'i')} {chip['message']}"), ("", "\n")]
        for i, (_, label, run) in enumerate(chip["chips"]):

            def click(event, i=i):
                if event.event_type == MouseEventType.MOUSE_UP:
                    self.press(i)

            style = "class:chip.off" if not run else ("class:chip.focus" if i == chip["focus"] else "class:chip")
            out += [(style, f" {i + 1} {label} ", click), ("", " ")]
        return out


class Provider:
    def __init__(self, name):
        self.name, self.session = name, None
        self.binary = shutil.which(name)
        if not self.binary:
            raise ChipError(name + " CLI를 설치한 뒤 같은 터미널에서 다시 실행해 주세요.")

    def turn(self, text):
        if self.name == "claude":
            cmd = [self.binary, "-p", "--output-format", "json"]
            if self.session:
                cmd += ["--resume", self.session]
        else:
            cmd = (
                [self.binary, "exec"]
                + (["resume"] if self.session else [])
                + ["--json", "--skip-git-repo-check"]
            )
            cmd += ([self.session] if self.session else []) + ["-"]
        proc = subprocess.run(cmd, input=text, stdout=subprocess.PIPE, encoding="utf-8")
        if proc.returncode:
            raise ChipError(f"{self.name} 실행이 종료 코드 {proc.returncode}로 실패했습니다.")
        if self.name == "claude":
            data = json.loads(proc.stdout)
            if data.get("is_error"):
                raise ChipError("Claude 요청이 실패했습니다. CLI 로그인을 확인해 주세요.")
            self.session = data["session_id"]
            return data["result"]
        messages = []
        for line in proc.stdout.splitlines():
            data = json.loads(line)
            if data.get("type") == "thread.started":
                self.session = data["thread_id"]
            item = data.get("item", {})
            if data.get("type") == "item.completed" and item.get("type") == "agent_message":
                messages.append(item["text"])
        return "\n".join(messages)


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"{TITLE} 실시간 프롬프트 입력")
    parser.add_argument("provider", choices=("claude", "codex"))
    args = parser.parse_args(argv)
    try:
        peer, provider = Peer(), Provider(args.provider)
        peer("status")
        answer = ""
        while True:
            peer("reset")  # 새 입력: 지난 입력의 원문유지·바꾼 값 기억을 지운다 (답변 복원은 남는다)
            composer = Composer(peer, args.provider, answer)
            text = composer.app.run()
            if text is None:
                break
            answer = provider.turn(text)
            print(answer)
    except (ChipError, ValueError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
