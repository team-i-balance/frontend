"""파일 검사: xlsx 보호본 (backend file_sanitizer + 앱 엔진). 모든 값은 합성 데이터다."""
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from agent import config
from agent.files import processor
from agent.files.processor import EngineProcessor, inspect_xlsx
from agent.sanitize.engine import Session
from agent.sanitize.restore import restore

from .test_pipeline import FailingModel, StubModel

BASE = config.load_base()
HAVE_DEPS = processor.available()


def cell_values(data: bytes) -> list[str]:
    from openpyxl import load_workbook
    book = load_workbook(BytesIO(data))
    try:
        return [str(c.value) for ws in book.worksheets for row in ws.iter_rows() for c in row if c.value is not None]
    finally:
        book.close()


@unittest.skipUnless(HAVE_DEPS, "openpyxl, defusedxml 필요")
class XlsxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.session = Session(BASE)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, book, name="명단.xlsx") -> Path:
        path = self.dir / name
        book.save(path)
        book.close()
        return path

    def sample(self, name="홍길동_상담기록.xlsx") -> Path:
        from openpyxl import Workbook
        from openpyxl.comments import Comment
        book = Workbook()
        ws = book.active
        ws.title = "상담명단"
        ws.append(["학생", "연락처", "메모"])
        ws.append(["홍길동 학생", "010-1234-5678", "다음 주 상담 예정"])
        ws.append(["홍길동", "010-2222-3333", "보호자 연락 완료"])  # 문맥 없는 이름: 다른 칸에서 확인된 값으로 가린다
        ws["C2"].comment = Comment("김민수 과장님 확인 필요", "작성자")
        hidden = book.create_sheet("숨김")
        hidden["A1"] = "이영희 님 010-9876-5432"
        hidden.sheet_state = "hidden"
        return self.save(book, name)

    def test_protected_copy_masks_cells_and_drops_hidden_parts(self):
        report = inspect_xlsx(self.sample(), BASE, self.session)
        self.assertEqual((report.status, report.grade), ("READY", "S"), report.reason)
        values = cell_values(report.file_bytes)
        joined = " ".join(values)
        for secret in ("홍길동", "010-1234-5678", "010-2222-3333", "김민수", "이영희", "010-9876-5432"):
            self.assertNotIn(secret, joined)
        fake = values[3].split()[0]  # B행이 아니라 A2 "○○○ 학생"
        self.assertIn(fake, values)   # A3도 같은 가짜 이름
        self.assertEqual(report.found["phone"], 2)
        self.assertGreaterEqual(report.found["name"], 2)
        self.assertGreaterEqual(report.hidden_found["name"], 2)  # 메모의 김민수, 숨긴 시트의 이영희
        self.assertEqual((report.summary.removed_comments, report.summary.removed_sheets), (1, 1))
        self.assertTrue(report.suggested_name.endswith("_보호본.xlsx"))
        self.assertNotIn("홍길동", report.suggested_name)
        # 프롬프트와 같은 매핑이라 AI 답변에서 원래 값으로 되돌릴 수 있다
        restored, _ = restore(f"{fake} 학생 상담 결과", self.session.entries)
        self.assertIn("홍길동", restored)

    def test_confidential_marker_blocks_whole_file(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(["대외비 2026 매출 계획", 100])
        report = inspect_xlsx(self.save(book), BASE, self.session)
        self.assertEqual((report.status, report.reason), ("BLOCKED", "CONFIDENTIAL"))
        self.assertIsNone(report.file_bytes)
        self.assertEqual(report.blocker_types["classified"], 1)

    def test_clean_file_is_ready_without_changes(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(["파이썬 딕셔너리 정렬", 3])
        report = inspect_xlsx(self.save(book), BASE, self.session)
        self.assertEqual((report.status, report.grade), ("READY", "O"))
        self.assertEqual(sum(report.found.values()), 0)
        self.assertEqual(cell_values(report.file_bytes), ["파이썬 딕셔너리 정렬", "3"])

    def test_unsupported_formula_and_extension(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active["A1"] = "=SUM(1,2)"
        self.assertEqual(inspect_xlsx(self.save(book), BASE, self.session).reason, "UNSUPPORTED_FEATURE")
        csv = self.dir / "a.csv"
        csv.write_text("홍길동,010-1234-5678", encoding="utf-8")
        self.assertEqual(inspect_xlsx(csv, BASE, self.session).reason, "UNSUPPORTED_FILE_TYPE")

    def test_model_results_are_used_and_failure_withholds_copy(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.append(["어제 김철수랑 회의했어"])
        path = self.save(book)
        report = inspect_xlsx(path, BASE, self.session, model=StubModel([("name", "김철수", 0.9)]))
        self.assertEqual((report.status, report.coverage), ("READY", "model"))
        self.assertNotIn("김철수", " ".join(cell_values(report.file_bytes)))
        failed = inspect_xlsx(path, BASE, Session(BASE), model=FailingModel())
        self.assertEqual((failed.status, failed.reason), ("FAILED", "MODEL_FAILED"))
        self.assertIsNone(failed.file_bytes)

    def test_verify_rejects_original_left_in_copy(self):
        from agent.files.xlsx.sanitizer import Segment
        proc = EngineProcessor(BASE, self.session)
        proc.originals = {"홍길동"}
        self.assertFalse(proc.verify("s", [Segment("s1:A1", "홍길동 보호자")]))
        self.assertTrue(proc.verify("s", [Segment("s1:A1", "보호자 연락 완료")]))


if __name__ == "__main__":
    unittest.main()
