"""Guarded terminal composer for Claude Code and Codex CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style

from .browser.actions import ChipError
from .browser.peer import Peer


def stable_ranges(spans, old, new):
    if old == new:
        return spans
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) - prefix and old[-1 - suffix] == new[-1 - suffix]:
        suffix += 1
    delta = len(new) - len(old)
    return [
        s if s["end"] < prefix else {**s, "start": s["start"] + delta, "end": s["end"] + delta}
        for s in spans
        if s["end"] < prefix or s["start"] > len(old) - suffix
    ]


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
            self.editor.select(False)
        return result


class Composer:
    def __init__(self, peer, provider, last_reply="", restore_ticket=None):
        self.peer, self.provider = peer, provider
        self.last_reply, self.restore_ticket = last_reply, restore_ticket
        self.spans, self.text, self.revision, self.checked = [], "", 0, -1
        self.selected = self.review = None
        self.plan, self.keep, self.reason = None, set(), None
        self.busy = self.working = False
        self.note = ""
        self.buffer = Buffer(multiline=True, on_text_changed=self.change)
        control = PromptControl(self)
        kb = KeyBindings()
        kb.add("c-c")(lambda e: self.app.exit(result=None))
        kb.add("c-j")(lambda e: self.buffer.insert_text("\n"))
        kb.add("escape")(lambda e: self.close())
        kb.add("f2")(lambda e: self.select(True))
        kb.add("f3")(lambda e: self.start("mask"))
        kb.add("f4")(lambda e: self.start("restore"))
        kb.add("f6")(lambda e: self.start("original"))
        kb.add("f7")(lambda e: self.start("detail"))
        kb.add("f8")(lambda e: self.start("files"))
        kb.add("f5")(lambda e: self.start("server_tools"))
        kb.add("c-m")(lambda e: self.submit())
        self.app = Application(
            layout=Layout(
                HSplit(
                    [
                        Window(
                            FormattedTextControl(
                                "KP Guard · "
                                + provider
                                + "   Enter 전송   Ctrl+J 줄바꿈   F2 작업   Ctrl+C 종료"
                            ),
                            height=1,
                        ),
                        Window(control, wrap_lines=True, height=5),
                        Window(FormattedTextControl(self.buttons), wrap_lines=True),
                        Window(FormattedTextControl(lambda: self.note), wrap_lines=True),
                    ]
                ),
                focused_element=control,
            ),
            key_bindings=kb,
            full_screen=False,
            mouse_support=True,
            style=Style.from_dict(
                {"hit": "bg:#5b4319 #fff1c6 underline", "chip": "bg:#40434b #ffffff", "title": "bold"}
            ),
        )

    def close(self):
        self.selected = self.review = None
        self.app.invalidate()

    def select(self, fallback):
        self.selected = next(
            (s for s in self.spans if s["start"] <= self.buffer.cursor_position < s["end"]), None
        )
        if fallback and not self.selected:
            self.selected = self.spans[0] if self.spans else {"label": "작업"}
        self.app.invalidate()

    def buttons(self):
        if not self.selected:
            return []
        result = [("class:title", self.selected["label"] + "  ")]
        options = [
            ("가릴 항목 선택", "inspect"),
            ("가명화 적용 F3", "mask"),
            ("원문 사용 F6", "original"),
            ("상세 점검 F7", "detail"),
            ("답변 복원 F4", "restore"),
            ("엑셀 보호 F8", "files"),
            ("서버 기능 전체 F5", "server_tools"),
            ("닫기", "close"),
        ]
        if self.plan:
            options = [
                (("원문 " if token in self.keep else "가림 ") + token, "toggle:" + token)
                for token in self.plan["choices"]
            ] + options
            if self.keep:
                options = [
                    ("오탐 사유", "reason:false_positive"),
                    ("업무상 필요 사유", "reason:business_need"),
                ] + options
        for label, action in options:

            def click(event, name=action):
                if event.event_type == MouseEventType.MOUSE_UP:
                    self.close() if name == "close" else self.start(name)

            result.extend([("class:chip", " " + label + " ", click), ("", " ")])
        return result

    def change(self, _):
        new = self.buffer.text
        self.spans = stable_ranges(self.spans, self.text, new)
        self.text, self.revision = new, self.revision + 1
        self.selected = self.review = self.plan = None
        self.keep, self.reason = set(), None
        self.note = "검사 중…" if new.strip() else ""
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
                            (data["coverage"] + (f" · {len(self.spans)}건" if self.spans else ""))
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

    def submit(self):
        if self.working or not self.text.strip():
            return
        if self.checked != self.revision:
            if not self.busy:
                self.change(self.buffer)
            return
        if self.spans:
            self.select(True)
            self.note = "표시된 항목을 가리거나 원문 사용을 선택해 주세요."
        else:
            self.app.exit(result=self.text)

    def start(self, action):
        if not self.working:
            self.working = True
            self.app.create_background_task(self.act(action))

    async def act(self, action):
        rev, text = self.revision, self.text

        async def call(name, **kw):
            return await asyncio.to_thread(self.peer, name, **kw)

        try:
            if action in ("files", "server_tools", "detail"):
                await call(action, **({"text": text} if action == "detail" else {}))
            elif action == "restore":
                if self.restore_ticket and self.last_reply:
                    data = await call("restore", ticket=self.restore_ticket, text=self.last_reply)
                    self.restore_ticket = None
                    self.note = "복원된 답변\n" + data["text"]
                else:
                    await call("desktop_restore", text=self.last_reply)
            elif action == "inspect" and text.strip():
                plan = await call("inspect", text=text)
                if rev == self.revision:
                    self.plan, self.keep, self.reason = plan, set(), None
                    self.note = plan["text"]
            elif action.startswith("toggle:") and self.plan:
                token = action.split(":", 1)[1]
                kept = self.keep ^ {token}
                out = await call("selection", ticket=self.plan["ticket"], keep=list(kept))
                if rev == self.revision:
                    self.keep, self.note = kept, out["text"]
            elif action.startswith("reason:"):
                self.reason = action.split(":", 1)[1]
                self.note = "원문 유지 사유를 선택했습니다."
            elif text.strip():
                if action == "mask" and self.keep and not self.reason:
                    raise ChipError("원문으로 유지할 항목의 사유를 선택해 주세요.")
                if action == "original" and self.review != rev:
                    self.review = rev
                    self.note = "원문을 전송하려면 F6을 다시 누르세요. 사유는 ‘선택 안 함’으로 기록됩니다."
                    return
                data = self.plan or await call("inspect", text=text)
                if rev != self.revision:
                    return
                out = await call(
                    "decide",
                    ticket=data["ticket"],
                    choice="original" if action == "original" else "apply",
                    reason="none" if action == "original" else self.reason,
                    keep=[] if action == "original" else list(self.keep),
                )
                if rev != self.revision:
                    return
                if out["canRestore"]:
                    self.restore_ticket = data["ticket"]
                if action == "original":
                    self.app.exit(result=text)
                else:
                    self.buffer.text = out["text"]
                    self.buffer.cursor_position = len(self.buffer.text)
                    self.note = "가명화를 적용했습니다. Enter로 전송합니다."
        except ChipError as exc:
            self.note = str(exc)
        finally:
            self.working = False
            self.app.invalidate()


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
    parser = argparse.ArgumentParser(description="KP Guard 실시간 프롬프트 입력")
    parser.add_argument("provider", choices=("claude", "codex"))
    args = parser.parse_args(argv)
    try:
        peer, provider = Peer(), Provider(args.provider)
        peer("status")
        answer, ticket = "", None
        while True:
            composer = Composer(peer, args.provider, answer, ticket)
            text = composer.app.run()
            if text is None:
                break
            ticket = composer.restore_ticket
            answer = provider.turn(text)
            print(answer)
    except (ChipError, ValueError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
