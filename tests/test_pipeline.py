"""탐지 파이프라인에 2단계 모델을 붙였을 때의 동작 (모델은 가짜 객체)."""
import unittest

from agent import config
from agent.detect.model import ModelError
from agent.detect.pipeline import MODEL_FAILED_NOTE, MODEL_MAX_CHARS, MODEL_SKIPPED_NOTE, detect
from agent.detect.span import Span
from agent.sanitize.engine import Session, make_items, sanitize

BASE = config.load_base()


class StubModel:
    """text → [(유형, 값, 신뢰도)]를 돌려주는 가짜 모델."""

    def __init__(self, found):
        self.found = found

    def detect(self, text):
        out = []
        for typ, value, conf in self.found:
            start = text.find(value)
            if start >= 0:
                out.append(Span(typ, start, start + len(value), value, conf, "S", "소형 모델(AEGIS) 판단", "model"))
        return out


class FailingModel:
    def detect(self, text):
        raise ModelError("failed")


def convert(text, model=None, session=None):
    det = detect(text, BASE, model=model)
    items = make_items(det, {})
    return det, sanitize(text, items, session or Session(BASE))


class PipelineModelTests(unittest.TestCase):
    def test_model_finds_name_rules_miss(self):
        text = "어제 김철수랑 회의했어"
        self.assertEqual(detect(text, BASE).spans, [])
        det, result = convert(text, StubModel([("name", "김철수", 0.9)]))
        self.assertEqual(det.coverage, "model")
        self.assertEqual([(s.type, s.value) for s in det.spans], [("name", "김철수")])
        self.assertNotIn("김철수", result.text)

    def test_partial_model_span_is_unioned_with_rule(self):
        text = "연락처 010-1234-5678 입니다"
        det, result = convert(text, StubModel([("phone", "010-1234", 0.95)]))
        self.assertEqual([s.value for s in det.spans], ["010-1234-5678"])
        self.assertNotIn("5678", result.text)

    def test_failure_is_reported_and_rules_still_apply(self):
        text = "연락처 010-1234-5678"
        det, result = convert(text, FailingModel())
        self.assertEqual(det.coverage, "model_failed")
        self.assertIn(MODEL_FAILED_NOTE, det.notes)
        self.assertNotIn("010-1234-5678", result.text)

    def test_long_text_skips_model_and_says_so(self):
        text = "어제 김철수랑 회의했어. " + "가" * MODEL_MAX_CHARS
        det = detect(text, BASE, model=StubModel([("name", "김철수", 0.9)]))
        self.assertEqual((det.coverage, det.spans), ("model_skipped", []))
        self.assertIn(MODEL_SKIPPED_NOTE, det.notes)
        full = detect(text, BASE, model=StubModel([("name", "김철수", 0.9)]), model_max_chars=None)
        self.assertEqual([s.value for s in full.spans], ["김철수"])

    def test_ignore_list_applies_to_model_results(self):
        det = detect("어제 김철수랑 회의했어", BASE, ignore={"김철수"}, model=StubModel([("name", "김철수", 0.9)]))
        self.assertEqual(det.spans, [])

    def test_low_confidence_model_result_needs_confirmation(self):
        det, result = convert("하나 씨에게 전달", StubModel([("name", "하나", 0.36)]))
        items = make_items(det, {})
        self.assertFalse(items[0].applied)
        self.assertIn("하나", result.text)

    def test_new_types_from_model(self):
        text = "생일 1995년 3월 15일, 면허 11-22-333333-44, 아이디 kim_minsu"
        det, result = convert(text, StubModel([("dob", "1995년 3월 15일", 0.9),
                                               ("id_number", "11-22-333333-44", 0.9),
                                               ("username", "kim_minsu", 0.9)]))
        self.assertIn("1990년대생", result.text)
        self.assertNotIn("333333", result.text)
        self.assertIn("[계정]", result.text)

    def test_known_values_catch_name_without_context(self):
        det = detect("홍길동 보호자 연락 완료", BASE, known=[("name", "홍길동", 0.8)])
        self.assertEqual([(s.type, s.value, s.source) for s in det.spans], [("name", "홍길동", "known")])

    def test_same_value_same_fake_across_prompt_and_model(self):
        session = Session(BASE)
        _, first = convert("홍길동 학생 상담", session=session)
        _, second = convert("어제 홍길동이랑 통화", StubModel([("name", "홍길동", 0.9)]), session)
        fake = first.text.split()[0]
        self.assertIn(fake, second.text)


if __name__ == "__main__":
    unittest.main()
