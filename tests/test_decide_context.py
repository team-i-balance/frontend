"""Issues #24-#27: decide carries who/where/which values, partial originals are flagged, reasons can follow."""

import json
import time
import unittest
from unittest.mock import Mock

from agent import config
from agent.browser.actions import BrowserActions
from agent.gui.reason_followup import ReasonFollowUp
from agent.server.client import DecideResult, ProcessResult, ServerClient, ServerError

PHONE = "010-2345-6789"


class CaptureClient(ServerClient):
    def __init__(self, reply):
        self.sent = []
        self.reply = reply

    def _request(self, method, path, body=None, timeout=None):
        self.sent.append((method, path, body))
        return self.reply


def wait_until(test, limit=2.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.01)
    return False


class DecideBodyTests(unittest.TestCase):
    def test_context_reason_text_and_reverted_are_sent(self):
        client = CaptureClient({"event": {"id": 41, "queue": "x", "reason_requested_at": None},
                                "outgoing_text": "t", "restore_available": False})
        out = client.decide("w1", "revert_original", "approved", reverted=["[이름_1]", "[이름_1]"],
                            context={"user_id": " kim ", "dept": "영업", "target_ai": "Claude (claude.ai)"})
        _, path, body = client.sent[0]
        self.assertEqual(path, "/process/decide")
        self.assertEqual(body["reason"], "business_need")       # 서버 값은 두 가지뿐
        self.assertEqual(body["reason_text"], "승인된 개인정보")   # 보안팀은 글로 구분한다 (#27)
        self.assertEqual(body["reverted"], ["[이름_1]"])
        self.assertEqual((body["user_id"], body["dept"], body["target_ai"]), ("kim", "영업", "Claude (claude.ai)"))
        self.assertEqual(out.event_id, 41)

    def test_empty_context_and_no_reason_send_nothing_extra(self):
        client = CaptureClient({"event": {"id": 3, "reason_requested_at": "2026-11-06T01:00:00"}})
        out = client.decide("w1", "send_as_is", None, reverted=["[x]"], context={"user_id": "", "dept": None})
        self.assertEqual(client.sent[0][2], {"work_id": "w1", "action": "send_as_is", "reason": None})
        self.assertTrue(out.reason_requested)

    def test_reason_api(self):
        client = CaptureClient({"event_id": 3, "reason_needed": True})
        self.assertTrue(client.reason_status(3)["reason_needed"])
        client.submit_reason(3, "business_need", " 고객 응대 ")
        self.assertEqual(client.sent[-1], ("POST", "/events/3/reason", {"reason": "business_need", "text": "고객 응대"}))
        with self.assertRaises(ServerError):
            client.submit_reason(3, "approved", "x")


class FixtureApp:
    def __init__(self):
        self.base = config.load_base()
        self.ignore = set()
        self.server = Mock()
        self.monitoring_active = Mock(return_value=True)
        self.server_mode = Mock(return_value=True)
        self.detector = Mock(return_value=None)
        self.post = Mock()

    @staticmethod
    def decide_context(target_ai=None):
        return {"user_id": "kim", "dept": "영업", "target_ai": target_ai}


class PartialOriginalTests(unittest.TestCase):
    def test_masking_one_value_and_keeping_another_is_revert_original(self):
        app = FixtureApp()
        actions = BrowserActions(app)
        server = app.server
        server.preview.side_effect = lambda sent: ([
            {"start": i, "end": i + len(v), "type": t}
            for v, t in ((PHONE, "PHONE"), ("김민수", "NAME")) for i in [sent.find(v)] if i >= 0], False)
        server.process.side_effect = lambda sent: ProcessResult(
            "w1", sent.replace(PHONE, "[전화번호_1]").replace("김민수", "[이름_1]"), ["PHONE", "NAME"], 2, "S",
            "high", 0, False)
        server.decide.return_value = DecideResult(None, False, None, 9)
        text = f"김민수 {PHONE}"
        spans = actions.preview(text, "tab")["spans"]
        phone = next(s for s in spans if s["label"] == "전화번호")
        chip = actions.mask("tab", text, phone["start"], phone["end"])
        actions.commit("tab", chip["ticket"], chip["keys"])
        after = text[:phone["start"]] + "[전화번호_1]" + text[phone["end"]:]
        name = actions.preview(after, "tab")["spans"][0]
        actions.keep("tab", after, name["start"], name["end"], "business_need")
        self.assertTrue(wait_until(lambda: server.decide.called))
        args, kwargs = server.decide.call_args
        self.assertEqual(args[1:], ("revert_original", "business_need"))
        self.assertEqual(kwargs["reverted"], ["[이름_1]"])
        self.assertEqual(kwargs["context"]["target_ai"], "Claude (claude.ai)")


class FollowUpTests(unittest.TestCase):
    def make(self):
        app = Mock()
        app.settings = {"reason_events": []}
        app.server_mode.return_value = True
        app.root.after = Mock()
        app.post = lambda cmd, fn, *a: fn(*a)
        return app

    def test_raw_sends_are_remembered_and_requests_open_a_dialog(self):
        app = self.make()
        follow = ReasonFollowUp(app)
        follow.note(DecideResult(None, False, None, 7), "send_transformed")  # 원문이 안 나감
        follow.note(DecideResult(None, False, None, 8), "revert_original")
        self.assertEqual([e["id"] for e in follow.events()], [8])
        json.dumps(app.settings)  # 설정 파일에 그대로 저장된다
        app.server.reason_status.return_value = {"reason_needed": True, "reason_requested_at": "2026-11-06T01:02"}
        opened = []
        import agent.gui.reason_followup as module
        original = module.ReasonRequestDialog
        module.ReasonRequestDialog = lambda f, event_id, at, count: opened.append((event_id, at, count)) or Mock()
        try:
            follow.check()
            self.assertTrue(wait_until(lambda: opened))
        finally:
            module.ReasonRequestDialog = original
        self.assertEqual(opened, [(8, "2026-11-06T01:02", 1)])
        follow.closed(8)  # 보냈다: 번호를 지운다
        self.assertEqual(follow.events(), [])

    def test_missing_events_are_dropped(self):
        app = self.make()
        follow = ReasonFollowUp(app)
        follow.note(DecideResult(None, False, None, 5), "send_as_is")
        app.server.reason_status.side_effect = ServerError("없음", "expired", 404)
        follow.check()
        self.assertTrue(wait_until(lambda: follow.events() == []))


if __name__ == "__main__":
    unittest.main()
