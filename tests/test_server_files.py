"""서버 처리 모드의 파일 검사: 업로드 → 결과 → 보호본 받기 (가짜 서버 상대). 모든 값은 합성 데이터다."""
import socket
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from agent import config
from agent.detect import lexicon
from agent.files import processor
from agent.files.remote import compare_copies, inspect_xlsx_server
from agent.sanitize.engine import Session
from agent.sanitize.restore import restore
from agent.server.align import premask
from agent.server.client import FileResult, Health, ServerClient, ServerError

from .fake_backend import FakeBackend
from .test_files import cell_values

BASE = config.load_base()
HAVE_DEPS = processor.available()


def closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # 아무도 듣지 않는 포트
    return port


def xlsx(rows, hidden_rows=None, comment=None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    book = Workbook()
    ws = book.active
    ws.title = "상담명단"
    for row in rows:
        ws.append(row)
    if comment:
        ws["A2"].comment = Comment(comment, "작성자")
    if hidden_rows:
        hidden = book.create_sheet("숨김")
        for row in hidden_rows:
            hidden.append(row)
        hidden.sheet_state = "hidden"
    out = BytesIO()
    book.save(out)
    book.close()
    return out.getvalue()


class OrgSpanTests(unittest.TestCase):
    def test_every_occurrence_of_an_org_value_is_premasked(self):
        text = "오로라 일정 공유드려요. 오로라 예산은 한빛전자 미팅 뒤에 정해요. 담당: 오로라"
        spans = lexicon.org_spans(text, BASE)
        self.assertEqual([s.value for s in spans].count("오로라"), 3)
        session = Session(BASE)
        sent, _ = premask(text, [(s.start, s.end, session.tag_for(s.type, s.value), "org", s.value) for s in spans])
        self.assertNotIn("오로라", sent)
        self.assertNotIn("한빛전자", sent)
        self.assertEqual(sent.count("[프로젝트A]"), 3)

    def test_ignored_values_are_left_alone(self):
        self.assertEqual([s.value for s in lexicon.org_spans("오로라 / 블루문", BASE, {"오로라"})], ["블루문"])


@unittest.skipUnless(HAVE_DEPS, "openpyxl, defusedxml 필요")
class FileClientTests(unittest.TestCase):
    DATA = None

    def setUp(self):
        self.data = xlsx([["학생", "연락처"], ["김민수", "010-1234-5678"]])

    def test_upload_then_download_only_once(self):
        with FakeBackend() as fake:
            client = ServerClient(fake.url, timeout=5)
            res = client.sanitize_file(self.data)
            self.assertEqual((res.status, res.grade, res.summary["changed_cells"]), ("READY", "S", 2))
            self.assertEqual(fake.uploads[0][0], "upload.xlsx")  # 원래 파일 이름은 보내지 않는다
            copy = client.download_file(res.work_id)
            self.assertEqual(cell_values(copy), ["학생", "연락처", "[이름_1]", "[전화번호_1]"])
            with self.assertRaises(ServerError) as ctx:
                client.download_file(res.work_id)
            self.assertEqual(ctx.exception.kind, "expired")

    def test_busy_server_is_retried_then_reported(self):
        waits = []
        with FakeBackend(busy=2) as fake:
            res = ServerClient(fake.url, timeout=5).sanitize_file(self.data, wait=waits.append)
        self.assertEqual((res.status, waits), ("READY", [1.0, 1.0]))
        with FakeBackend(busy=10) as fake:
            res = ServerClient(fake.url, timeout=5).sanitize_file(self.data, retries=2, wait=waits.append)
        self.assertEqual((res.status, res.reason, res.http_status), ("FAILED", "FILE_PROCESSING_BUSY", 429))

    def test_server_without_model_unreachable_and_odd_ids(self):
        with FakeBackend(model=False, legacy_health=True) as fake:
            client = ServerClient(fake.url, timeout=5)
            res = client.sanitize_file(self.data)
            self.assertEqual((res.status, res.reason, res.http_status), ("FAILED", "MODEL_UNAVAILABLE", 503))
            with self.assertRaises(ServerError) as ctx:
                client.download_file("../health")
            self.assertEqual(ctx.exception.kind, "bad_response")
            self.assertNotIn("/health", fake.requests)
        with self.assertRaises(ServerError) as ctx:
            ServerClient(f"http://127.0.0.1:{closed_port()}", timeout=2).sanitize_file(self.data)
        self.assertEqual(ctx.exception.kind, "unreachable")


class StubClient:
    """보호본을 바꿔 치는 서버 흉내 (받은 보호본을 이 PC에서 다시 검사하는지 본다)."""

    def __init__(self, copy: bytes):
        self.copy = copy
        self.uploaded = None

    def health(self):
        return Health(True, "model")

    def sanitize_file(self, data):
        self.uploaded = data
        return FileResult("w1", "READY", "S", None, {"changed_cells": 1})

    def download_file(self, work_id):
        return self.copy


@unittest.skipUnless(HAVE_DEPS, "openpyxl, defusedxml 필요")
class ServerInspectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.session = Session(BASE)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data: bytes, name="홍길동_상담기록.xlsx") -> Path:
        path = self.dir / name
        path.write_bytes(data)
        return path

    def sample(self) -> Path:
        return self.write(xlsx([["학생", "연락처", "메모"],
                                ["홍길동", "010-1234-5678", "오로라 일정 확인"],
                                ["김민수", "010-2222-3333", "한빛전자 미팅, 오로라 예산"]],
                               hidden_rows=[["이지은 님 010-9876-5432"]], comment="이지은 학부모 상담"))

    def test_only_visible_values_without_org_base_reach_the_server(self):
        stages = []
        with FakeBackend() as fake:
            report = inspect_xlsx_server(self.sample(), BASE, self.session, ServerClient(fake.url, timeout=5),
                                         progress=stages.append)
            self.assertEqual(fake.files, {})  # 보호본은 받는 즉시 서버에서 지워진다
        name, uploaded = fake.uploads[0]
        self.assertEqual((report.status, report.grade, report.where), ("READY", "S", "server"))
        self.assertEqual(stages, ["check", "local", "upload", "download"])
        self.assertEqual(name, "upload.xlsx")
        sent = " ".join(cell_values(uploaded))
        for value in ("오로라", "한빛전자", "이지은", "010-9876-5432"):  # 조직 베이스, 숨긴 시트, 메모
            self.assertNotIn(value, sent)
        from openpyxl import load_workbook
        self.assertEqual(load_workbook(BytesIO(uploaded)).sheetnames, ["시트1"])  # 시트 이름도 바뀐 채로 간다

        cells = cell_values(report.file_bytes)
        self.assertEqual(cells, ["학생", "연락처", "메모", "[이름_1]", "[전화번호_1]", "[프로젝트A] 일정 확인",
                                 "[이름_2]", "[전화번호_2]", "[고객사A] 미팅, [프로젝트A] 예산"])
        self.assertEqual(dict(report.found), {"name": 2, "phone": 2, "codename": 2, "client": 1})
        self.assertEqual(dict(report.org_found), {"codename": 2, "client": 1})
        self.assertEqual(report.server_tokens, {"[이름_1]", "[이름_2]", "[전화번호_1]", "[전화번호_2]"})
        self.assertEqual((report.summary.removed_comments, report.summary.removed_sheets), (1, 1))
        self.assertEqual(report.suggested_name, "[이름_1]_상담기록_보호본.xlsx")
        # 조직 베이스 태그는 이 PC의 매핑으로 되돌린다
        self.assertEqual(restore("[프로젝트A] 일정은 [고객사A]와", self.session.entries)[0], "오로라 일정은 한빛전자와")

    def test_confidential_marker_blocks_before_upload(self):
        path = self.write(xlsx([["구분", "내용"], ["등급", "대외비 자료"], ["담당", "김민수"]]))
        with FakeBackend() as fake:
            report = inspect_xlsx_server(path, BASE, self.session, ServerClient(fake.url, timeout=5))
        self.assertEqual((report.status, report.reason, report.grade), ("BLOCKED", "CONFIDENTIAL", "C"))
        self.assertTrue(report.blocker_reasons)
        self.assertEqual(fake.uploads, [])

    def test_server_block_and_server_side_problems(self):
        with FakeBackend() as fake:
            path = self.write(xlsx([["설정"], ["api_key=sk-test-0000"]]))
            report = inspect_xlsx_server(path, BASE, self.session, ServerClient(fake.url, timeout=5))
        self.assertEqual((report.status, report.reason, report.fallback), ("BLOCKED", "SERVER_CONFIDENTIAL", False))
        # 서버가 /health로 규칙만이라고 알려 주면 파일을 보내지 않는다 (backend#11)
        with FakeBackend(model=False) as fake:
            report = inspect_xlsx_server(self.sample(), BASE, self.session, ServerClient(fake.url, timeout=5))
        self.assertEqual((report.status, report.reason, report.fallback), ("FAILED", "SERVER_RULES_ONLY", True))
        self.assertEqual(fake.uploads, [])
        self.assertTrue(report.message)
        # 탐지 범위를 알려 주지 않는 서버는 올려 보고 503을 받는다
        with FakeBackend(model=False, legacy_health=True) as fake:
            report = inspect_xlsx_server(self.sample(), BASE, self.session, ServerClient(fake.url, timeout=5))
        self.assertEqual((report.status, report.reason, report.fallback), ("FAILED", "MODEL_UNAVAILABLE", True))
        self.assertEqual(len(fake.uploads), 1)
        client = ServerClient(f"http://127.0.0.1:{closed_port()}", timeout=2)
        report = inspect_xlsx_server(self.sample(), BASE, self.session, client)
        self.assertEqual((report.status, report.reason, report.fallback), ("FAILED", "SERVER_UNREACHABLE", True))
        report = inspect_xlsx_server(self.sample(), BASE, self.session, None)
        self.assertEqual((report.reason, report.fallback), ("SERVER_UNSET", True))

    def test_file_problems_stop_on_this_pc(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active["A1"] = "=1+1"
        out = BytesIO()
        book.save(out)
        with FakeBackend() as fake:
            client = ServerClient(fake.url, timeout=5)
            report = inspect_xlsx_server(self.write(out.getvalue(), "수식.xlsx"), BASE, self.session, client)
            self.assertEqual((report.status, report.reason, report.fallback), ("FAILED", "UNSUPPORTED_FEATURE", False))
            report = inspect_xlsx_server(self.write(b"a,b", "표.csv"), BASE, self.session, client)
            self.assertEqual(report.reason, "UNSUPPORTED_FILE_TYPE")
        self.assertEqual(fake.uploads, [])

    def test_copy_from_server_is_checked_against_the_upload(self):
        good = xlsx([["학생"], ["[이름_1]"]])
        path = self.write(xlsx([["학생"], ["김민수"]]))
        report = inspect_xlsx_server(path, BASE, self.session, StubClient(good))
        self.assertEqual(report.status, "READY")
        # 올린 사본에 없던 칸, 숨긴 시트, 이 PC에서 가린 값이 되살아난 보호본은 쓰지 않는다
        for bad in (xlsx([["학생", "추가"], ["[이름_1]"]]), xlsx([["학생"], ["[이름_1]"]], hidden_rows=[["x"]]),
                    xlsx([["학생"], ["오로라"]]), b"PK not really a workbook"):
            report = inspect_xlsx_server(path, BASE, self.session, StubClient(bad))
            self.assertEqual((report.status, report.reason, report.fallback), ("FAILED", "SERVER_COPY_INVALID", True))
            self.assertIsNone(report.file_bytes)

    def test_compare_counts_only_new_placeholders(self):
        before = xlsx([["메모"], ["[이름_1] 표기는 원래 있던 글자, 김민수"]])
        after = xlsx([["메모"], ["[이름_1] 표기는 원래 있던 글자, [이름_2]"]])
        diff = compare_copies(before, after)
        self.assertEqual((dict(diff.found), diff.tokens, diff.values), ({"name": 1}, {"[이름_2]"}, {"김민수": "[이름_2]"}))


if __name__ == "__main__":
    unittest.main()
