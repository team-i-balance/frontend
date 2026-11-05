import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent import config
from agent.browser.actions import BrowserActions, ChipError
from agent.browser.bridge import BrowserBridge
from agent.browser.peer import Peer
from agent.cli import Composer, Provider, stable_ranges
from agent.server.client import ProcessResult, ServerError


class FixtureApp:
    def __init__(self):
        self.base = config.load_base()
        self.ignore = set()
        self.server = Mock()
        self.monitoring_active = Mock(return_value=True)
        self.server_mode = Mock(return_value=True)
        self.detector = Mock(return_value=None)
        self.post = Mock()


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.app = FixtureApp()
        self.actions = BrowserActions(self.app)

    def test_live_inspects_clean_and_sensitive_text_on_server(self):
        peer = self.app.server
        phone = "010-2345-6789"
        text = "🧪 연락 " + phone
        peer.preview.side_effect = [([], False), ([{"start": 5, "end": 18, "type": "PHONE"}], False)]
        result = self.actions.preview("한")
        self.assertEqual(result["spans"], [])
        result = self.actions.preview(text)
        self.assertEqual(text[result["spans"][0]["start"] : result["spans"][0]["end"]], phone)
        self.assertEqual([c.args[0] for c in peer.preview.call_args_list], ["한", text])
        peer.process.assert_not_called()
        self.assertFalse(self.actions.works)

    def test_org_premask_maps_server_indices_to_original(self):
        self.app.base["projects"] = []  # use a controlled org finder below
        text = "긴프로젝트명 뒤 전화 010-2345-6789"
        org = SimpleNamespace(start=0, end=6, length=6, type="codename", value=text[:6])

        def preview(sent):
            self.assertNotIn(text[:6], sent)
            ix = sent.index("010")
            return [{"start": ix, "end": ix + 13, "type": "PHONE"}], True

        self.app.server.preview.side_effect = preview
        with patch("agent.browser.actions.lexicon.org_spans", return_value=[org]):
            spans = self.actions.preview(text)["spans"]
        self.assertEqual([text[s["start"] : s["end"]] for s in spans], [text[:6], "010-2345-6789"])

    def test_local_mode_and_pause_do_not_call_team_server(self):
        self.app.server_mode.return_value = False
        req = {"action": "preview", "channel": "tab", "text": "연락 010-2345-6789"}
        self.assertEqual(self.actions.handle(req)["spans"][0]["label"], "전화번호")
        self.app.monitoring_active.return_value = False
        with self.assertRaises(ChipError):
            self.actions.handle(req)
        self.app.server.preview.assert_not_called()

    def test_legacy_server_highlights_only_exact_ranges(self):
        app = self.app
        app.server.preview.side_effect = ServerError("missing", "expired", 404)
        app.server.process.return_value = ProcessResult(
            "w", "연락 [전화번호_1]", ["PHONE"], 1, "S", "low", 0, True
        )
        for _ in range(2):
            result = self.actions.preview("연락 010-2345-6789")
            self.assertEqual(result["spans"], [{"start": 3, "end": 16, "label": "전화번호", "keep": True}])
        self.assertEqual(app.server.preview.call_count, 1)
        app.server.process.return_value.masked_text = "[이름_1][이름_2]"
        app.server.process.return_value.detected_count = 2
        result = self.actions.preview("두사람")
        self.assertFalse(result["spans"])
        self.assertEqual(result["unlocated"], 2)

    def test_pairing_proof_and_origin(self):
        bridge = BrowserBridge("k" * 43, self.actions, port=0)
        bridge.start()
        self.addCleanup(bridge.stop)
        peer = Peer("k" * 43, bridge.server_port)
        self.assertTrue(peer("status")["active"])
        with self.assertRaises(ChipError):
            Peer("x" * 43, bridge.server_port)("preview", text="비공개")
        self.app.server.preview.assert_not_called()
        req = Request(
            peer.base + "challenge",
            data=b"{}",
            headers={"Origin": "https://example.org", "Content-Type": "application/json"},
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(req)
        self.assertEqual(caught.exception.code, 403)

    def test_highlights_track_unaffected_text(self):
        hit = {"start": 1, "end": 4, "label": "검사"}
        self.assertEqual(stable_ranges([hit], " abc xyz", " abc xyz!"), [hit])
        self.assertEqual(stable_ranges([hit], " abc xyz", " axc xyz"), [])

    def test_provider_stdin_and_conversation_resume(self):
        for name in ("claude", "codex"):
            output = (
                json.dumps({"session_id": "s1", "result": "OK"})
                if name == "claude"
                else "\n".join(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "s1"}),
                        json.dumps(
                            {"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}}
                        ),
                    ]
                )
            )
            with (
                patch("agent.cli.shutil.which", return_value="provider"),
                patch("agent.cli.subprocess.run") as run,
            ):
                run.return_value = SimpleNamespace(returncode=0, stdout=output)
                provider = Provider(name)
                self.assertEqual(provider.turn("첫 문장"), "OK")
                self.assertEqual(provider.turn("다음 문장"), "OK")
                args, kwargs = run.call_args
                self.assertIn("s1", args[0])
                self.assertNotIn("다음 문장", args[0])
                self.assertEqual(kwargs["input"], "다음 문장")


class LiveCliLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_first_edit_and_queues_latest_without_idle_timer(self):
        import threading

        began, proceed = threading.Event(), threading.Event()
        calls = []

        def peer(action, text):
            calls.append(text)
            if len(calls) == 1:
                began.set()
                proceed.wait(2)
            return {"coverage": "서버", "spans": []}

        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input import DummyInput
        from prompt_toolkit.output import DummyOutput

        with create_app_session(input=DummyInput(), output=DummyOutput()):
            editor = Composer(peer, "codex")
        editor.app = Mock()
        editor.app.create_background_task = asyncio.create_task
        editor.buffer.text = "첫"
        await asyncio.to_thread(began.wait, 2)
        editor.buffer.text = "첫 번"
        editor.buffer.text = "첫 번째 입력"
        proceed.set()
        for _ in range(100):
            if not editor.busy:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(calls, ["첫", "첫 번째 입력"])
        self.assertEqual(editor.checked, editor.revision)


class TerminalInputTests(unittest.IsolatedAsyncioTestCase):
    """보호된 CLI 입력창: 하이라이트가 남으면 Enter를 막고 칩을 연다. 칩은 숫자·Enter·Esc로 고른다."""

    async def run_cli(self, script):
        from prompt_toolkit.application import create_app_session
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        app = FixtureApp()
        app.server_mode.return_value = False
        operations = BrowserActions(app)

        def peer(op, **kw):
            return operations.handle({"action": op, "channel": "terminal", **kw})

        async def until(predicate):
            async def wait():
                while not predicate():
                    await asyncio.sleep(0.015)

            await asyncio.wait_for(wait(), 3)

        with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
            cli = Composer(peer, "codex")
            cli.app.ttimeoutlen = 0.05
            task = asyncio.create_task(cli.app.run_async())
            try:
                await script(cli, inp, until)
                return await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    cli.app.exit(result=None)
                    await task

    async def test_enter_is_blocked_and_mask_edits_only_the_value(self):
        async def script(cli, inp, until):
            inp.send_text("연락 010-2345-6789 부탁")
            await until(lambda: cli.spans and cli.checked == cli.revision)
            inp.send_bytes(b"\r")  # 하이라이트가 있으면 보내지 않고 칩을 연다
            await until(lambda: cli.chip)
            self.assertEqual([c[0] for c in cli.chip["chips"]], ["mask", "keep"])
            self.assertIn("보내기 전에", cli.chip["note"])
            inp.send_text("1")  # 마스킹
            await until(lambda: cli.chip.get("chips") and cli.chip["chips"][0][0] == "apply" and cli.chip["chips"][0][2])
            self.assertTrue(cli.chip["word"])
            inp.send_bytes(b"\r")  # 고른 칩(적용)
            await until(lambda: "010-2345-6789" not in cli.text)
            self.assertTrue(cli.text.startswith("연락 ") and cli.text.endswith(" 부탁"))
            self.assertEqual([c[0] for c in cli.chip["chips"]], ["undo"])
            await until(lambda: cli.checked == cli.revision and not cli.spans)
            inp.send_bytes(b"\x1b")
            await until(lambda: cli.chip is None)
            inp.send_bytes(b"\r")

        sent = await self.run_cli(script)
        self.assertNotIn("010-2345-6789", sent)

    async def test_keep_needs_a_reason_then_sends_the_original(self):
        async def script(cli, inp, until):
            inp.send_text("연락 010-2345-6789")
            await until(lambda: cli.spans and cli.checked == cli.revision)
            inp.send_bytes(b"\x1bOQ")  # F2
            await until(lambda: cli.chip)
            inp.send_text("2")  # 원문유지
            await until(lambda: [c[1] for c in cli.chip["chips"]] == ["업무상 필요", "오탐", "승인된 개인정보"])
            inp.send_text("1")
            await until(lambda: not cli.spans and cli.chip and cli.chip.get("done"))
            inp.send_bytes(b"\x1b")
            await until(lambda: cli.chip is None)
            inp.send_bytes(b"\r")

        self.assertEqual(await self.run_cli(script), "연락 010-2345-6789")
