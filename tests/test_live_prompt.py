import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agent import config
from agent.browser.actions import BrowserActions, ChipError
from agent.browser.bridge import BrowserBridge
from agent.browser.peer import Peer
from agent.overlay.live import stable_ranges
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
