"""2단계 소형 모델(AEGIS) 연동 테스트. 실제 모델 대신 tests/toy_model.py의 가짜 모델을 쓴다."""
import tempfile
import time
import unittest
from pathlib import Path

from agent.detect import model as m
from agent.detect.model import AegisModel, ModelError, ModelManager, ModelUnavailable

from . import toy_model

ID2LABEL = {"0": "O", "1": "B-SURNAME", "2": "B-GIVENNAME", "3": "I-GIVENNAME",
            "4": "B-CITY", "5": "B-STREET", "6": "I-STREET", "7": "I-TELEPHONENUM"}


def char_tokens(text, labels):
    """글자 하나 = 토큰 하나. labels: 글자 위치 → 라벨 번호 (없으면 O)."""
    offsets, ids = [(0, 0)], [0]
    for i, ch in enumerate(text):
        if not ch.isspace():
            offsets.append((i, i + 1))
            ids.append(labels.get(i, 0))
    offsets.append((0, 0))
    ids.append(0)
    return offsets, ids, [0.9] * len(ids)


class DecodeTests(unittest.TestCase):
    def test_surname_and_given_name_become_one_name_without_josa(self):
        text = "김민수의 연락처"
        offsets, ids, probs = char_tokens(text, {0: 1, 1: 2, 2: 3, 3: 3})
        spans = m.to_spans(text, m.merge_windows(m.decode(text, offsets, ids, probs, ID2LABEL)))
        self.assertEqual([(s.type, s.value, s.source) for s in spans], [("name", "김민수", "model")])
        self.assertAlmostEqual(spans[0].confidence, 0.9)

    def test_two_people_stay_separate(self):
        text = "김민수 이지은"
        offsets, ids, probs = char_tokens(text, {0: 1, 1: 2, 2: 3, 4: 1, 5: 2, 6: 3})
        spans = m.to_spans(text, m.decode(text, offsets, ids, probs, ID2LABEL))
        self.assertEqual([s.value for s in spans], ["김민수", "이지은"])

    def test_address_parts_merge_and_city_only_is_low_confidence(self):
        text = "서울 테헤란로 근처, 서울"
        offsets, ids, probs = char_tokens(text, {0: 4, 1: 4, 3: 5, 4: 6, 5: 6, 6: 6, 12: 4, 13: 4})
        spans = m.to_spans(text, m.decode(text, offsets, ids, probs, ID2LABEL))
        self.assertEqual([s.value for s in spans], ["서울 테헤란로", "서울"])
        self.assertAlmostEqual(spans[0].confidence, 0.9)
        self.assertLess(spans[1].confidence, 0.6)  # 지역만 → 확인 필요

    def test_unknown_label_is_an_error(self):
        with self.assertRaises(ModelError):
            m.decode("김", [(0, 1)], [99], [0.9], ID2LABEL)

    def test_overlapping_windows_merge(self):
        a = {"start": 0, "end": 8, "type": "phone", "raw": {"TELEPHONENUM"}, "probs": [0.9], "head": "TELEPHONENUM"}
        b = {"start": 4, "end": 13, "type": "phone", "raw": {"TELEPHONENUM"}, "probs": [0.8], "head": "TELEPHONENUM"}
        merged = m.merge_windows([a, b])
        self.assertEqual([(s["start"], s["end"]) for s in merged], [(0, 13)])

    def test_josa_trimming_keeps_name_endings(self):
        cases = {"김민수의": "김민수", "김민준이": "김민준", "이민호가": "이민호", "민수님께서": "민수", "강남구에": "강남구",
                 "김유나": "김유나", "김사랑": "김사랑", "테헤란로": "테헤란로", "김지이": "김지이", "김하은": "김하은",
                 "민아": "민아"}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(value[:m.trim_josa(value, 0, len(value))], expected)


class LoadingTests(unittest.TestCase):
    def test_onnxruntime_telemetry_is_disabled_before_import(self):
        import os
        self.assertEqual(os.environ.get("ORT_DISABLE_TELEMETRY"), "1")

    def test_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ModelUnavailable):
                AegisModel(tmp)
            self.assertFalse(m.looks_like_model_dir(tmp))

    @unittest.skipUnless(toy_model.deps_available(), "onnx, onnxruntime, tokenizers 필요")
    def test_corrupt_model_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = toy_model.build(tmp)
            (folder / "model.onnx").write_bytes(b"not a model")
            with self.assertRaises(ModelUnavailable):
                AegisModel(folder)

    def test_manager_reports_error_and_off(self):
        states = []
        manager = ModelManager(on_change=lambda: states.append(manager.state))
        with tempfile.TemporaryDirectory() as tmp:
            manager.configure(tmp)
            deadline = time.time() + 10
            while manager.state == "loading" and time.time() < deadline:
                time.sleep(0.02)
        self.assertEqual(manager.state, "error")
        self.assertIsNone(manager.detector())
        self.assertIn("모델 폴더", manager.message)
        manager.configure("")
        self.assertEqual(manager.state, "off")
        self.assertEqual(states[0], "loading")


@unittest.skipUnless(toy_model.deps_available(), "onnx, onnxruntime, tokenizers 필요")
class ToyModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = toy_model.build(Path(cls.tmp.name) / "aegis")
        cls.model = AegisModel(cls.folder)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_detects_name_and_phone_on_device(self):
        text = "김민수의 번호는 010-1234-5678 이에요"
        spans = self.model.detect(text)
        self.assertEqual([(s.type, s.value) for s in spans], [("name", "김민수"), ("phone", "010-1234-5678")])
        self.assertTrue(all(0.9 < s.confidence <= m.MAX_CONFIDENCE for s in spans))

    def test_low_confidence_is_reported_as_low(self):
        spans = self.model.detect("하나 씨에게 전달")
        self.assertEqual([s.value for s in spans], ["하나"])
        self.assertLess(spans[0].confidence, 0.6)

    def test_long_text_is_checked_in_overlapping_windows(self):
        text = "가" * 500 + " 010-1234-5678 " + "가" * 600 + " 김민수"
        spans = self.model.detect(text)
        self.assertEqual([(s.type, s.value) for s in spans], [("phone", "010-1234-5678"), ("name", "김민수")])

    def test_cache_returns_copies(self):
        text = "김민수 010-2222-3333"
        first = self.model.detect(text)
        first[0].confidence = 0.0
        again = self.model.detect(text)
        self.assertGreater(again[0].confidence, 0.9)

    def test_manager_loads_in_background(self):
        manager = ModelManager()
        manager.configure(self.folder)
        deadline = time.time() + 30
        while manager.state == "loading" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(manager.state, "ready", manager.message)
        self.assertIsNotNone(manager.detector())


if __name__ == "__main__":
    unittest.main()
