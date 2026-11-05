"""서버 처리: API 클라이언트(가짜 서버 상대)와 원문 맞추기."""
import socket
import unittest

from agent.server.align import align, compose, premask, to_orig
from agent.server.client import ServerClient, ServerError, is_insecure

from .fake_backend import FakeBackend


class ClientTests(unittest.TestCase):
    def test_process_decide_restore_round_trip(self):
        with FakeBackend() as fake:
            client = ServerClient(fake.url, timeout=5)
            self.assertTrue(client.health())
            res = client.process("김민수 님 연락처 010-1234-5678")
            self.assertEqual((res.grade, res.confidence, res.rules_only), ("S", "high", False))
            self.assertEqual(res.masked_text, "[이름_1] 님 연락처 [전화번호_1]")
            out = client.decide(res.work_id, "send_transformed")
            self.assertTrue(out.restore_available)
            restored = client.restore(res.work_id, "[이름_1] 님께 [전화번호_1]로 연락드릴게요")
            self.assertEqual(restored.text, "김민수 님께 010-1234-5678로 연락드릴게요")
            with self.assertRaises(ServerError) as ctx:  # 복원은 한 번만
                client.restore(res.work_id, "[이름_1]")
            self.assertEqual(ctx.exception.kind, "expired")

    def test_falls_back_to_rules_only_when_server_has_no_model(self):
        with FakeBackend(model=False) as fake:
            client = ServerClient(fake.url, timeout=5)
            res = client.process("김민수 010-1234-5678")
            self.assertEqual((res.rules_only, res.confidence), (True, "low"))
            self.assertEqual(res.masked_text, "김민수 [전화번호_1]")
            self.assertFalse(client.model_available)
            client.process("두 번째 요청")
            self.assertEqual(fake.requests.count("/process"), 3)  # 400 한 번 뒤로는 바로 규칙 모드

    def test_reasons_are_mapped_to_server_values(self):
        with FakeBackend() as fake:
            client = ServerClient(fake.url, timeout=5)
            for reason in ("approved", "none", "false_positive"):
                res = client.process("010-1234-5678")
                client.decide(res.work_id, "revert_original", reason)
            self.assertEqual([d["reason"] for d in fake.decisions], ["business_need", None, "false_positive"])

    def test_errors_never_carry_text(self):
        with FakeBackend() as fake:
            client = ServerClient(fake.url, timeout=5)
            with self.assertRaises(ServerError) as ctx:
                client.process("")
            self.assertEqual(ctx.exception.kind, "rejected")
            with self.assertRaises(ServerError) as ctx:
                client.decide("no-such-work", "cancel")
            self.assertEqual(ctx.exception.kind, "expired")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # 아무도 듣지 않는 포트
        with self.assertRaises(ServerError) as ctx:
            ServerClient(f"http://127.0.0.1:{port}", timeout=2).process("홍길동 010-1234-5678")
        self.assertEqual(ctx.exception.kind, "unreachable")
        self.assertNotIn("홍길동", str(ctx.exception))

    def test_url_checks(self):
        self.assertTrue(is_insecure("http://13.193.207.69:8501"))
        self.assertFalse(is_insecure("https://guard.example.com"))
        self.assertFalse(is_insecure("http://127.0.0.1:8000"))
        with self.assertRaises(ValueError):
            ServerClient("13.193.207.69:8501")


class AlignTests(unittest.TestCase):
    def test_values_found_for_each_placeholder(self):
        items = align("김민수의 연락처는 010-1234-5678입니다.", "[이름_1]의 연락처는 [전화번호_1]입니다.")
        self.assertEqual([(i.token, i.value, i.start, i.end) for i in items],
                         [("[이름_1]", "김민수", 0, 3), ("[전화번호_1]", "010-1234-5678", 10, 23)])

    def test_repeated_placeholders_and_literal_tokens(self):
        items = align("김민수, 김민수, 이지은", "[이름_1], [이름_1], [이름_2]")
        self.assertEqual([i.value for i in items], ["김민수", "김민수", "이지은"])
        items = align("[이름_1] 김민수", "[이름_1] [이름_2]")  # 원문에 원래 있던 자리표시 글자는 빼고
        self.assertEqual([(i.token, i.value) for i in items], [("[이름_2]", "김민수")])

    def test_ambiguous_positions_are_not_exact(self):
        items = align("홍 길동 010-1234-5678", "[이름_1] [전화번호_1]")  # 공백이 이름 안에도 있음
        self.assertFalse(any(i.exact for i in items))
        self.assertTrue(all(i.value is None for i in items))
        self.assertEqual(align("A김민수010", "A[이름_1][전화번호_1]")[0].exact, False)  # 붙은 자리표시

    def test_compose_reverts_and_extra_masks_only_outside_placeholders(self):
        masked = "[이름_1]의 오로라 일정, [전화번호_1]"
        items = align("김민수의 오로라 일정, 010-1234-5678", masked)
        segs = compose(masked, items, reverted={"[이름_1]"}, extra=[("오로라", "[프로젝트A]", "org")])
        self.assertEqual("".join(s for s, _ in segs), "김민수의 [프로젝트A] 일정, [전화번호_1]")
        unsure = align("홍 길동 010", "[이름_1] [전화번호_1]")
        segs = compose("[이름_1] [전화번호_1]", unsure, reverted={"[이름_1]"})  # 위치 불명은 되돌리지 않음
        self.assertEqual("".join(s for s, _ in segs), "[이름_1] [전화번호_1]")


class PremaskTests(unittest.TestCase):
    def test_org_values_never_reach_server_and_positions_map_back(self):
        text = "김민수 님, 오로라 프로젝트 건으로 010-1234-5678 연락"
        org = text.index("오로라 프로젝트")
        sent, repls = premask(text, [(org, org + 8, "[프로젝트A]", "org", "오로라 프로젝트")])
        self.assertEqual(sent, "김민수 님, [프로젝트A] 건으로 010-1234-5678 연락")
        with FakeBackend() as fake:
            res = ServerClient(fake.url, timeout=5).process(sent)
        self.assertNotIn("오로라", " ".join(fake.received))
        items = align(sent, res.masked_text)
        phone = next(i for i in items if i.label == "전화번호")
        start, end = to_orig(phone.start, repls), to_orig(phone.end, repls)
        self.assertEqual(text[start:end], "010-1234-5678")
        self.assertIsNone(to_orig(repls[0].s_start + 2, repls))  # 태그 안쪽

    def test_overlapping_spans_keep_the_first_longest(self):
        sent, repls = premask("abcdef", [(1, 3, "[X]", "manual", "bc"), (1, 5, "[Y]", "org", "bcde")])
        self.assertEqual((sent, [r.value for r in repls]), ("a[Y]f", ["bcde"]))


if __name__ == "__main__":
    unittest.main()
