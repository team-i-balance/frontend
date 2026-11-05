"""칩 흐름: 마스킹(바뀔 값 → 입력창 그 자리만 바꿈) / 원문유지(사유 셋) / 남은 하이라이트가 없을 때 한 번 기록."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import config
from agent.browser.actions import KEEP_REASONS, BrowserActions, ChipError
from agent.server.client import DecideResult, ProcessResult, RestoreResult


class FixtureApp:
    def __init__(self, server_mode=True):
        self.base = config.load_base()
        self.ignore = set()
        self.server = Mock()
        self.monitoring_active = Mock(return_value=True)
        self.server_mode = Mock(return_value=server_mode)
        self.detector = Mock(return_value=None)
        self.post = Mock()


def apply(text, edits):
    for a, b, new in reversed(edits):
        text = text[:a] + new + text[b:]
    return text


def wait_until(test, limit=2.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.01)
    return False


PHONE = "010-2345-6789"


class ServerChipTests(unittest.TestCase):
    def setUp(self):
        self.app = FixtureApp()
        self.actions = BrowserActions(self.app)
        self.server = self.app.server

    def spans_for(self, sent):
        out = []
        for value, kind in ((PHONE, "PHONE"), ("김민수", "NAME")):
            start = sent.find(value)
            while start >= 0:
                out.append({"start": start, "end": start + len(value), "type": kind})
                start = sent.find(value, start + len(value))
        return out, False

    def process_for(self, sent):
        masked = sent.replace(PHONE, "[전화번호_1]").replace("김민수", "[이름_1]")
        return ProcessResult("w-" + str(len(sent)), masked, ["PHONE", "NAME"], 2, "S", "high", 0, False)

    def test_mask_shows_only_the_word_and_edits_the_value_in_place(self):
        self.server.preview.side_effect = self.spans_for
        self.server.process.side_effect = self.process_for
        self.server.decide.return_value = DecideResult(None, True, "pre_warning")
        text = f"김민수 님 번호 {PHONE}, 다시 {PHONE}"
        spans = self.actions.preview(text, "tab")["spans"]
        self.assertEqual([s["label"] for s in spans], ["이름", "전화번호", "전화번호"])
        phone = spans[1]
        chip = self.actions.handle({"action": "mask", "channel": "tab", "text": text,
                                    "start": phone["start"], "end": phone["end"]})
        self.assertEqual(chip["word"], "[전화번호_1]")
        self.assertEqual(chip["count"], 2)  # 같은 값은 함께 바뀐다
        self.assertNotIn("text", chip)       # 프롬프트 전체가 아니라 바뀔 값만
        with self.assertRaises(ChipError):   # 다른 탭은 이 칩을 쓸 수 없다
            self.actions.handle({"action": "commit", "channel": "tab2", "ticket": chip["ticket"],
                                 "keys": chip["keys"]})
        self.actions.handle({"action": "commit", "channel": "tab", "ticket": chip["ticket"], "keys": chip["keys"]})
        after = apply(text, chip["edits"])
        self.assertEqual(after, "김민수 님 번호 [전화번호_1], 다시 [전화번호_1]")
        self.server.decide.assert_not_called()  # 이름이 아직 남았다
        spans = self.actions.preview(after, "tab")["spans"]
        self.assertEqual([s["label"] for s in spans], ["이름"])
        # 같은 작업을 이어서 쓴다 (서버 /process 한 번)
        name = self.actions.mask("tab", after, spans[0]["start"], spans[0]["end"])
        self.assertEqual(self.server.process.call_count, 1)
        self.actions.commit("tab", name["ticket"], name["keys"])
        final = apply(after, name["edits"])
        self.assertEqual(final, "[이름_1] 님 번호 [전화번호_1], 다시 [전화번호_1]")
        self.assertEqual(self.actions.preview(final, "tab")["spans"], [])
        self.assertTrue(wait_until(lambda: self.server.decide.called))
        self.server.decide.assert_called_once()
        self.assertEqual(self.server.decide.call_args.args, (self.process_for(text).work_id, "send_transformed", None))
        self.assertIsNone(self.server.decide.call_args.kwargs["reverted"])  # 전부 가렸다
        self.server.restore.return_value = RestoreResult(f"답: 김민수 {PHONE}", [], [])
        reply = self.actions.handle({"action": "restore", "channel": "tab", "text": "답: [이름_1] [전화번호_1]"})
        self.assertEqual(reply["text"], f"답: 김민수 {PHONE}")
        with self.assertRaises(ChipError):  # 서버는 한 번만 복원한다
            self.actions.handle({"action": "restore", "channel": "tab", "text": "답"})

    def test_mask_every_value_and_undo(self):
        self.server.preview.side_effect = self.spans_for
        self.server.process.side_effect = self.process_for
        text = f"김민수 {PHONE}"
        spans = self.actions.preview(text, "tab")["spans"]
        chip = self.actions.mask("tab", text, spans[0]["start"], spans[0]["end"], every=True)
        self.assertEqual(sorted(chip["keys"]), ["[이름_1]", "[전화번호_1]"])
        self.actions.commit("tab", chip["ticket"], chip["keys"])
        after = apply(text, chip["edits"])
        self.assertEqual(after, "[이름_1] [전화번호_1]")
        back = self.actions.undo("tab", chip["ticket"], ["[이름_1]"])
        self.assertEqual(apply(after, back["edits"]), "김민수 [전화번호_1]")
        self.actions.commit("tab", chip["ticket"], ["[이름_1]"], applied=False)
        spans = self.actions.preview("김민수 [전화번호_1]", "tab")["spans"]
        self.assertEqual([s["label"] for s in spans], ["이름"])

    def test_keep_needs_one_of_three_reasons_and_records_once(self):
        self.assertEqual(list(KEEP_REASONS.values()), ["업무상 필요", "오탐", "승인된 개인정보"])
        self.server.preview.side_effect = self.spans_for
        self.server.process.side_effect = self.process_for
        self.server.decide.return_value = DecideResult(None, False, "violation_candidate")
        text = f"고객 {PHONE} 으로 연락"
        span = self.actions.preview(text, "tab")["spans"][0]
        req = {"action": "keep", "channel": "tab", "text": text, "start": span["start"], "end": span["end"]}
        with self.assertRaises(ChipError):
            self.actions.handle({**req, "reason": "none"})
        out = self.actions.handle({**req, "reason": "approved"})
        self.assertEqual(out["reason"], "승인된 개인정보")
        self.assertEqual(self.actions.preview(text, "tab")["spans"], [])
        self.assertTrue(wait_until(lambda: self.server.decide.called))
        self.server.decide.assert_called_once()
        self.assertEqual(self.server.decide.call_args.args[1:], ("send_as_is", "approved"))
        self.actions.preview(text + " 부탁", "tab")
        time.sleep(0.05)
        self.server.decide.assert_called_once()  # 같은 원문유지를 두 번 기록하지 않는다

    def test_false_positive_is_not_detected_again(self):
        self.server.preview.side_effect = self.spans_for
        self.server.process.side_effect = self.process_for
        text = f"주문번호 {PHONE}"
        span = self.actions.preview(text, "tab")["spans"][0]
        self.actions.keep("tab", text, span["start"], span["end"], "false_positive")
        self.assertIn(PHONE, self.app.ignore)
        self.assertEqual(self.actions.preview(f"다른 탭 {PHONE}", "other")["spans"], [])

    def test_secrets_cannot_be_kept(self):
        self.server.preview.return_value = ([{"start": 4, "end": 24, "type": "API_KEY"}], False)
        text = "키는 sk-abcdefghijklmnopqrstu 야"
        span = self.actions.preview(text, "tab")["spans"][0]
        self.assertFalse(span["keep"])
        with self.assertRaises(ChipError):
            self.actions.keep("tab", text, span["start"], span["end"], "business_need")

    def test_organization_values_are_masked_locally_and_never_sent(self):
        org = SimpleNamespace(start=0, end=6, length=6, type="codename", value="긴프로젝트명")
        text = f"긴프로젝트명 담당 {PHONE}"

        def process(sent):
            self.assertNotIn("긴프로젝트명", sent)
            return self.process_for(sent)

        self.server.process.side_effect = process
        with patch("agent.browser.actions.lexicon.org_spans", return_value=[org]):
            chip = self.actions.mask("tab", text, 0, 6)
        self.assertTrue(chip["word"].startswith("[프로젝트"))
        self.assertEqual(apply(text, chip["edits"]), f"{chip['word']} 담당 {PHONE}")

    def test_unaligned_highlight_gets_a_type_tag(self):
        self.server.process.return_value = ProcessResult("w", "아무 변화 없음", [], 0, "O", "high", 0, False)
        chip = self.actions.mask("tab", "아무 변화 없음", 3, 5, label="이름")
        self.assertEqual(chip["word"], "[이름]")
        self.assertEqual(chip["edits"], [[3, 5, "[이름]"]])


class LocalChipTests(unittest.TestCase):
    def test_local_mask_fixes_josa_and_hides_inserted_fakes(self):
        app = FixtureApp(server_mode=False)
        actions = BrowserActions(app)
        text = "홍길동 학생이 010-1234-5678로 연락했어요"
        spans = actions.preview(text, "cli")["spans"]
        phone = next(s for s in spans if s["label"] == "전화번호")
        chip = actions.mask("cli", text, phone["start"], phone["end"])
        self.assertTrue(chip["word"])
        actions.commit("cli", chip["ticket"], chip["keys"])
        after = apply(text, chip["edits"])
        self.assertNotIn("010-1234-5678", after)
        left = actions.preview(after, "cli")["spans"]
        self.assertNotIn("전화번호", [s["label"] for s in left])  # 넣은 가짜 번호는 다시 표시하지 않는다
        app.server.process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
