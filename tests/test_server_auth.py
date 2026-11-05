"""서버 인증(Bearer 토큰: 팀·개발용)과 HTTPS 인증서 고정 (backend#10). 가짜 서버 상대, 합성 데이터만."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent import config
from agent.files import processor
from agent.files.remote import inspect_xlsx_server
from agent.sanitize.engine import Session
from agent.server.client import (ServerClient, ServerError, format_fingerprint, normalize_fingerprint, server_key,
                                 valid_token)

from .fake_backend import CERTS, FakeBackend, fingerprint

TEXT = "홍길동 학생 연락처 010-1234-5678"
TEAM = "team-token-abc123"


def bearer(fake, path):
    """그 경로로 받은 Authorization 헤더들."""
    return [header for seen, header in fake.auth_seen if seen == path]


class TokenTests(unittest.TestCase):
    def test_dev_token_is_issued_once_and_sent_only_where_needed(self):
        with FakeBackend(auth=True) as fake:
            client = ServerClient(fake.url, timeout=5, dev_token=True)
            self.assertEqual(client.auth_mode, "dev")
            self.assertTrue(client.health().ok)
            res = client.process(TEXT)
            client.decide(res.work_id, "send_transformed")
            self.assertEqual(len(fake.issued), 1)
            token = next(iter(fake.issued))
            self.assertEqual(bearer(fake, "/process"), [f"Bearer {token}"])
            self.assertEqual(bearer(fake, "/process/decide"), [f"Bearer {token}"])
            self.assertEqual(bearer(fake, "/health"), [None])        # 공개 경로에는 토큰을 보내지 않는다
            self.assertEqual(bearer(fake, "/auth/dev-token"), [None])

    def test_dev_token_is_renewed_once_after_server_restart(self):
        with FakeBackend(auth=True) as fake:
            client = ServerClient(fake.url, timeout=5, dev_token=True)
            client.process(TEXT)
            fake.restart()  # 배포로 서버를 다시 시작하면 개발용 토큰을 잊는다
            res = client.process(TEXT)
            self.assertEqual(res.masked_text, "[이름_1] 학생 연락처 [전화번호_1]")
            self.assertEqual(fake.requests.count("/auth/dev-token"), 2)
            self.assertEqual(fake.requests.count("/process"), 3)    # 401 한 번 → 새 토큰으로 다시
            self.assertEqual(fake.received, [TEXT, TEXT])           # 글은 401에 막혀 처리되지 않았다

    def test_server_that_refuses_dev_tokens_is_forbidden_before_any_text(self):
        with FakeBackend(auth=True, dev_tokens=False) as fake:
            with self.assertRaises(ServerError) as ctx:
                ServerClient(fake.url, timeout=5, dev_token=True).process(TEXT)
            self.assertEqual(ctx.exception.kind, "forbidden")
            self.assertIn("팀 토큰", ctx.exception.message)
            self.assertNotIn("/process", fake.requests)

    def test_team_token(self):
        with FakeBackend(auth=True, dev_tokens=False, team_tokens={TEAM}) as fake:
            client = ServerClient(fake.url, timeout=5, token=f"  {TEAM}\n")
            self.assertEqual((client.auth_mode, client.check_auth()), ("team", "ok"))
            self.assertEqual(client.process(TEXT).masked_text, "[이름_1] 학생 연락처 [전화번호_1]")
            for kwargs in ({"token": "wrong-token"}, {}):
                with self.assertRaises(ServerError) as ctx:
                    ServerClient(fake.url, timeout=5, **kwargs).process("김민수 010-2222-3333")
                self.assertEqual((ctx.exception.kind, ctx.exception.status), ("unauthorized", 401))
                self.assertNotIn("wrong-token", ctx.exception.message)
            self.assertEqual(fake.received, [TEXT])  # 인증에 실패한 글은 처리되지 않았다
            self.assertEqual(fake.requests.count("/auth/dev-token"), 0)  # 팀 토큰은 개발용 토큰을 받지 않는다

    def test_check_auth_sends_no_text(self):
        with FakeBackend(auth=True, team_tokens={TEAM}) as fake:
            self.assertEqual(ServerClient(fake.url, timeout=5, token=TEAM).check_auth(), "ok")
            with self.assertRaises(ServerError) as ctx:
                ServerClient(fake.url, timeout=5, token="nope-nope").check_auth()
            self.assertEqual(ctx.exception.kind, "unauthorized")
            self.assertEqual(fake.received, [])
        with FakeBackend() as fake:  # 확인 경로(/policy/current)가 없는 이전 서버
            fake.policy = False
            self.assertEqual(ServerClient(fake.url, timeout=5).check_auth(), "unknown")

    def test_server_without_auth_works_without_a_token(self):
        # backend#10 전 서버: /auth/dev-token이 없으면 토큰 없이 보낸다
        with FakeBackend(auth=False) as fake:
            client = ServerClient(fake.url, timeout=5, dev_token=True)
            self.assertEqual(client.process(TEXT).grade, "S")
            self.assertTrue(all(header is None for _, header in fake.auth_seen))

    def test_tokens_never_go_over_plain_http_to_another_host(self):
        for kwargs in ({"token": TEAM}, {"dev_token": True}):  # 연결하기 전에 멈춘다 (10.0.0.1에는 닿지 않는다)
            client = ServerClient("http://10.0.0.1:8501", timeout=1, **kwargs)
            with self.assertRaises(ServerError) as ctx:
                client.process(TEXT)
            self.assertEqual(ctx.exception.kind, "insecure")
        with FakeBackend(auth=True, team_tokens={TEAM}) as fake:  # 이 PC 안의 서버는 HTTP여도 된다
            self.assertEqual(ServerClient(fake.url, timeout=5, token=TEAM).check_auth(), "ok")

    def test_redirects_are_not_followed_with_the_token(self):
        with FakeBackend(auth=True) as other, FakeBackend(auth=True, team_tokens={TEAM}) as fake:
            fake.redirect_to = other.url + "/health"
            client = ServerClient(fake.url, timeout=5, token=TEAM)
            status, _, _ = client._send("GET", "/redirect", None, {})
            self.assertEqual(status, 307)
            self.assertEqual(other.requests, [])  # 다른 서버로 토큰이 가지 않았다

    def test_token_format(self):
        self.assertTrue(valid_token("abc-DEF_123.~+/=="))
        for bad in ("", "a b", "x\r\nX-Evil: 1", "토큰", 123, None):
            self.assertFalse(valid_token(bad))
        with self.assertRaises(ValueError):
            ServerClient("https://127.0.0.1", token="bad token")


class CertificateTests(unittest.TestCase):
    def test_pinned_self_signed_certificate_is_trusted(self):
        with FakeBackend(auth=True, tls="other") as fake:  # 자체 서명 (팀 서버 Caddy와 같은 방식)
            client = ServerClient(fake.url, timeout=5, dev_token=True, pin=fingerprint("other"))
            self.assertEqual((client.cert_mode, client.health().ok), ("pinned", True))
            self.assertEqual(client.process(TEXT).masked_text, "[이름_1] 학생 연락처 [전화번호_1]")

    def test_unpinned_untrusted_certificate_can_be_probed_without_sending_anything(self):
        with FakeBackend(auth=True, tls="server") as fake:
            client = ServerClient(fake.url, timeout=5, dev_token=True)
            with self.assertRaises(ServerError) as ctx:
                client.process(TEXT)
            self.assertEqual(ctx.exception.kind, "untrusted_cert")
            self.assertEqual(client.probe_certificate(), fingerprint("server"))
            self.assertEqual(fake.received, [])
            self.assertEqual(fake.issued, set())  # 지문 읽기에는 토큰을 받거나 보내지 않는다
            self.assertTrue(all(header is None for _, header in fake.auth_seen))

    def test_pin_mismatch_stops_before_the_request(self):
        with FakeBackend(auth=True, tls="server", team_tokens={TEAM}) as fake:
            client = ServerClient(fake.url, timeout=5, token=TEAM, pin=fingerprint("other"))
            for call in (client.health, lambda: client.process(TEXT), client.check_auth):
                with self.assertRaises(ServerError) as ctx:
                    call()
                self.assertEqual(ctx.exception.kind, "pin_mismatch")
                self.assertEqual(ctx.exception.fingerprint, fingerprint("server"))
            self.assertEqual(fake.requests, [])  # 요청 줄 하나도 서버에 닿지 않았다 (토큰·글 모두)

    def test_rotated_server_certificate_is_caught(self):
        with FakeBackend(auth=True, tls="server") as fake:
            client = ServerClient(fake.url, timeout=5, dev_token=True, pin=fingerprint("server"))
            self.assertTrue(client.health().ok)
            fake.use_cert("other")
            with self.assertRaises(ServerError) as ctx:
                client.process(TEXT)
            self.assertEqual((ctx.exception.kind, ctx.exception.fingerprint), ("pin_mismatch", fingerprint("other")))

    def test_system_trust_store_without_pin(self):
        with FakeBackend(auth=True, tls="server") as fake, \
                mock.patch.dict(os.environ, {"SSL_CERT_FILE": str(CERTS / "ca.pem")}):
            client = ServerClient(fake.url, timeout=5, dev_token=True)
            self.assertEqual((client.cert_mode, client.health().ok), ("system", True))

    def test_https_to_a_plain_http_port_is_a_tls_error(self):
        with FakeBackend() as fake:
            with self.assertRaises(ServerError) as ctx:
                ServerClient(fake.url.replace("http://", "https://"), timeout=5).health()
            self.assertEqual(ctx.exception.kind, "tls")

    def test_fingerprint_formats(self):
        value = fingerprint("server")
        colons = format_fingerprint(value)
        self.assertEqual(len(colons), 95)
        for text in (colons, colons.lower(), f"sha256 Fingerprint={colons}", f"SHA256:{value}", f" {value} ",
                     colons.replace(":", " ")):
            self.assertEqual(normalize_fingerprint(text), value)
        for bad in ("", "abc", value[:-2], value + "00", "zz" * 32, None):
            self.assertIsNone(normalize_fingerprint(bad))
        self.assertEqual(server_key("https://13.193.207.69"), "13.193.207.69:443")
        self.assertEqual(server_key("https://Guard.Example.com:8443/"), "guard.example.com:8443")
        self.assertEqual(server_key("http://127.0.0.1"), "127.0.0.1:80")
        with self.assertRaises(ValueError):
            ServerClient("https://127.0.0.1", pin="not-a-fingerprint")
        self.assertIsNone(ServerClient("http://127.0.0.1:8000", pin=value).pin)  # HTTP에는 인증서가 없다


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        for name in ("KPGUARD_SERVER_URL", "KPGUARD_API_TOKEN", "KPGUARD_SERVER_PIN"):
            os.environ.pop(name, None)

    def test_old_plain_http_default_moves_to_https(self):
        path = Path(self.tmp.name) / config.APP_NAME / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"server_url": config.OLD_SERVER_URL}), encoding="utf-8")
        self.assertEqual(config.load_settings()["server_url"], "https://13.193.207.69")
        path.write_text(json.dumps({"server_url": "http://127.0.0.1:8000"}), encoding="utf-8")
        self.assertEqual(config.load_settings()["server_url"], "http://127.0.0.1:8000")  # 바꾼 주소는 그대로

    def test_pins_are_per_server_and_not_shared_with_defaults(self):
        settings = config.load_settings()
        settings["server_pins"]["13.193.207.69:443"] = fingerprint("other")
        self.assertEqual(config.DEFAULTS["server_pins"], {})
        self.assertEqual(config.server_pin(settings, "https://13.193.207.69"), fingerprint("other"))
        self.assertEqual(config.server_pin(settings, "https://13.193.207.69:8443"), "")

    def test_auth_settings_and_managed_overrides(self):
        settings = config.load_settings()
        self.assertEqual((config.auth_mode(settings), config.api_token(settings)), ("dev", ""))
        settings.update(auth_mode="team", api_token=f" {TEAM} ")
        self.assertEqual((config.auth_mode(settings), config.api_token(settings)), ("team", TEAM))
        settings["auth_mode"] = "dev"
        self.assertEqual(config.api_token(settings), "")  # 개발용 토큰을 고르면 팀 토큰을 보내지 않는다
        with mock.patch.dict(os.environ, {"KPGUARD_API_TOKEN": "managed-token", "KPGUARD_SERVER_PIN": "ab" * 32}):
            self.assertEqual((config.auth_mode(settings), config.api_token(settings)), ("team", "managed-token"))
            self.assertEqual(config.server_pin(settings, "https://other.example"), "ab" * 32)


@unittest.skipUnless(processor.available(), "openpyxl, defusedxml 필요")
class FileAuthTests(unittest.TestCase):
    def setUp(self):
        from .test_server_files import xlsx
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "상담기록.xlsx"
        self.path.write_bytes(xlsx([["학생", "연락처"], ["홍길동", "010-1234-5678"]]))
        self.base = config.load_base()

    def inspect(self, client):
        return inspect_xlsx_server(self.path, self.base, Session(self.base), client)

    def test_file_flow_with_dev_token_over_pinned_https(self):
        with FakeBackend(auth=True, tls="other") as fake:
            report = self.inspect(ServerClient(fake.url, timeout=5, dev_token=True, pin=fingerprint("other")))
            self.assertEqual((report.status, report.grade), ("READY", "S"))
            token = next(iter(fake.issued))
            self.assertEqual(bearer(fake, "/files/sanitize"), [f"Bearer {token}"])
            self.assertEqual(len([p for p, _ in fake.auth_seen if p.endswith("/download")]), 1)

    def test_file_flow_reports_auth_and_certificate_problems(self):
        cases = [
            (dict(auth=True, team_tokens={TEAM}), dict(token="wrong-token"), "SERVER_UNAUTHORIZED"),
            (dict(auth=True, dev_tokens=False), dict(dev_token=True), "SERVER_FORBIDDEN"),
            (dict(auth=True, tls="server"), dict(dev_token=True), "SERVER_CERT_UNTRUSTED"),
            (dict(auth=True, tls="server"), dict(dev_token=True, pin=fingerprint("other")), "SERVER_CERT_MISMATCH"),
        ]
        for server, client, reason in cases:
            with self.subTest(reason=reason), FakeBackend(**server) as fake:
                report = self.inspect(ServerClient(fake.url, timeout=5, **client))
                self.assertEqual((report.status, report.reason, report.fallback), ("FAILED", reason, True))
                self.assertEqual(fake.uploads, [])  # 파일은 서버에 닿지 않았다
                self.assertTrue(processor.REASON_MESSAGES[reason])


if __name__ == "__main__":
    unittest.main()
