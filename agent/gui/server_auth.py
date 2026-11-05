"""서버 인증·인증서 대화상자 (backend#10): 토큰 방식(개발용 / 팀)과 HTTPS 인증서 지문 고정.

팀 서버는 도메인 없이 자체 서명 인증서를 쓴다. 처음 연결할 때 서버가 보낸 인증서 지문을 보여 주고,
팀에서 받은 지문과 같은지 확인한 뒤에 고정하게 한다. 고정한 뒤에는 지문이 다른 서버에 아무것도 보내지 않는다.
"""
from __future__ import annotations

import os
import tkinter as tk
from urllib.parse import urlsplit

from .. import config
from ..server.client import format_fingerprint, normalize_fingerprint, server_key
from . import theme

SERVER_CERT_COMMAND = "openssl x509 -in /etc/caddy/selfsigned/cert.pem -noout -fingerprint -sha256"
MODE_HELP = {
    "dev": "서버가 바로 내주는 개발용 토큰을 받아 써요. 서버를 다시 시작하면 자동으로 새로 받아요. "
           "운영 서버는 개발용 토큰을 막아 둘 수 있어요.",
    "team": "관리자에게 받은 팀 토큰을 넣으세요. 이 PC 사용자 폴더의 설정 파일에만 저장해요.",
}


def two_lines(fingerprint: str) -> str:
    """32바이트 지문을 16바이트씩 두 줄로 (바이트 중간에서 끊기지 않게)."""
    full = format_fingerprint(fingerprint)
    return f"{full[:47]}\n{full[48:]}" if full else ""


def short_fingerprint(fingerprint: str) -> str:
    """AB:CD:EF:01…89:AB (앞 4바이트·뒤 2바이트)."""
    full = format_fingerprint(fingerprint)
    return f"{full[:11]}…{full[-5:]}" if full else ""


class ServerAuthDialog(theme.Dialog):
    def __init__(self, app, master):
        super().__init__(master, "서버 인증·인증서")
        self.app = app
        self.probed: str | None = None  # 서버가 보낸 인증서 지문 (읽은 값)
        px, wrap = theme.px, theme.px(472)
        url = config.server_url(app.settings)
        self.https = urlsplit(url).scheme == "https"
        self.env_token = bool(os.environ.get("KPGUARD_API_TOKEN"))
        self.env_pin = bool(os.environ.get("KPGUARD_SERVER_PIN"))

        theme.label(self.body, f"서버  {url or '주소 없음'}", "label14r", wraplength=wrap).pack(fill="x")

        # ---------------------------------------------------------------- 인증 토큰
        self._section("인증 토큰")
        self.v_mode = tk.StringVar(value=config.auth_mode(app.settings))
        self.seg = theme.Segmented(self.body, [("dev", "개발용 토큰"), ("team", "팀 토큰")], self.v_mode,
                                   self._mode_changed)
        self.seg.pack(fill="x")
        self.mode_help = theme.label(self.body, "", "caption12", wraplength=wrap)
        self.mode_help.pack(fill="x", pady=(px(8), 0))
        self.token_row = tk.Frame(self.body, bg=theme.BG_RAISED)
        self.v_token = tk.StringVar(value=app.settings.get("api_token", ""))
        self.token_input = theme.Input(self.token_row, textvariable=self.v_token)
        self.token_input.entry.configure(show="•")
        self.token_input.pack(side="left", fill="x", expand=True)
        self.btn_show = theme.Button(self.token_row, "보이기", self._toggle_show, kind="ghost", size="sm")
        self.btn_show.pack(side="left", padx=(px(8), 0))
        self.token_note = theme.label(self.body, "", "caption12", theme.TEXT_DANGER, wraplength=wrap)
        if self.env_token:
            self.mode_help.configure(text="KPGUARD_API_TOKEN 환경 변수의 팀 토큰을 써요. 여기서는 바꿀 수 없어요.")

        # ---------------------------------------------------------------- 서버 인증서
        self._section("서버 인증서 (HTTPS)")
        self.cert_state = theme.label(self.body, "", "label14r", wraplength=wrap)
        self.cert_state.pack(fill="x")
        self.cert_warn = theme.label(self.body, "", "label14r", theme.TEXT_DANGER, wraplength=wrap)
        self.cert_warn.pack(fill="x")
        theme.label(self.body, "서버가 보낸 인증서 지문 (SHA-256)", "caption12").pack(fill="x", pady=(px(8), 0))
        self.fp_label = tk.Label(self.body, text="", font=theme.F["code12"], bg=theme.BG_RAISED,
                                 fg=theme.TEXT_PRIMARY, anchor="w", justify="left", wraplength=wrap)
        self.fp_label.pack(fill="x")
        theme.label(self.body, "팀에서 받은 지문 (붙여넣으면 바로 비교해요)", "caption12").pack(
            fill="x", pady=(px(8), px(4)))
        self.v_team_fp = tk.StringVar()
        self.v_team_fp.trace_add("write", lambda *a: self._compare())
        self.fp_input = theme.Input(self.body, textvariable=self.v_team_fp)
        self.fp_input.pack(fill="x")
        self.compare = theme.label(self.body, "", "label14r", wraplength=wrap)
        self.compare.pack(fill="x", pady=(px(4), 0))
        crow = tk.Frame(self.body, bg=theme.BG_RAISED)
        crow.pack(fill="x", pady=(px(8), 0))
        self.btn_trust = theme.Button(crow, "이 지문 신뢰", self._trust, size="sm")
        self.btn_trust.pack(side="left")
        self.btn_probe = theme.Button(crow, "다시 읽기", self._probe, kind="ghost", size="sm")
        self.btn_probe.pack(side="left", padx=(px(8), 0))
        self.btn_forget = theme.Button(crow, "신뢰 해제", self._forget, kind="ghost", size="sm")
        self.btn_forget.pack(side="left", padx=(px(8), 0))
        theme.label(self.body, f"지문은 서버 관리자에게 받으세요. 서버에서 확인: {SERVER_CERT_COMMAND}",
                    "caption12", theme.TEXT_TERTIARY, wraplength=wrap).pack(fill="x", pady=(px(8), 0))

        self.btn_save = theme.Button(self.buttons, "저장·연결 확인", self._save, kind="primary")
        self.btn_save.pack(side="right")
        theme.Button(self.buttons, "닫기", self.destroy).pack(side="right", padx=px(8))

        self._mode_changed()
        self.refresh()
        if self.https:
            self._probe()
        self.update_idletasks()
        self.geometry(f"+{master.winfo_rootx() + px(120)}+{master.winfo_rooty() + px(40)}")
        self.grab_set()
        self.after(1500, self._tick)

    def _tick(self) -> None:
        """연결 확인은 작업 스레드에서 끝나므로, 열려 있는 동안 상태를 다시 그린다."""
        if self.winfo_exists():
            self.refresh()
            self.after(1500, self._tick)

    def _section(self, title: str) -> None:
        tk.Label(self.body, text=title, font=theme.F["body16"], bg=theme.BG_RAISED, fg=theme.TEXT_PRIMARY,
                 anchor="w").pack(fill="x", pady=(theme.px(20), theme.px(8)))

    # ================================================================== 토큰
    def _mode_changed(self) -> None:
        mode = self.v_mode.get()
        if not self.env_token:
            self.mode_help.configure(text=MODE_HELP[mode])
        if mode == "team" and not self.env_token:
            self.token_row.pack(fill="x", pady=(theme.px(8), 0), after=self.mode_help)
            self.token_note.pack(fill="x", after=self.token_row)
        else:
            self.token_row.pack_forget()
            self.token_note.pack_forget()
        if self.env_token:
            self.seg.pack_forget()

    def _toggle_show(self) -> None:
        hidden = self.token_input.entry.cget("show") != ""
        self.token_input.entry.configure(show="" if hidden else "•")
        self.btn_show.configure(text="숨기기" if hidden else "보이기")

    def _save(self) -> None:
        mode = self.v_mode.get()
        if not self.env_token:
            if mode == "team" and not self.v_token.get().strip():
                self.token_note.configure(text="팀 토큰을 넣거나 개발용 토큰을 고르세요.")
                return
            problem = self.app.set_auth(mode, self.v_token.get() if mode == "team" else None)
            if problem:
                self.token_note.configure(text=problem)
                return
        elif self.app.server_mode():
            self.app.check_server()
        self.destroy()

    # ================================================================== 인증서
    def refresh(self) -> None:
        """지금 고정한 지문과 연결 상태를 보여 준다."""
        url = config.server_url(self.app.settings)
        pin = normalize_fingerprint(config.server_pin(self.app.settings, url) or "")
        error = self.app.server_error
        if not self.https:
            self.cert_state.configure(text="HTTP 주소라 인증서가 없어요. 토큰과 글은 HTTPS(또는 이 PC 안의 서버)로만 보내요.")
        elif pin:
            source = "KPGUARD_SERVER_PIN 환경 변수" if self.env_pin else server_key(url)
            self.cert_state.configure(text=f"지금: 지문 고정 ({source})\n{short_fingerprint(pin)}")
        else:
            self.cert_state.configure(text="지금: 고정한 지문 없음 · 시스템 인증서 저장소로 확인해요")
        warn = ""
        if error is not None and error.kind == "pin_mismatch":
            warn = ("서버 인증서가 신뢰한 것과 달라요. 팀에 서버 인증서를 바꿨는지 확인하기 전에는 다시 신뢰하지 마세요. "
                    "가로채기일 수 있어요.")
        elif error is not None and error.kind == "untrusted_cert":
            warn = "시스템이 믿지 않는 인증서예요 (자체 서명). 팀에서 받은 지문과 같은지 확인한 뒤 신뢰하세요."
        self.cert_warn.configure(text=warn)
        self.cert_warn.pack_configure(pady=(theme.px(4), 0) if warn else 0)
        can_edit = self.https and not self.env_pin
        self.btn_forget.configure(state="normal" if can_edit and pin else "disabled")
        self.btn_probe.configure(state="normal" if self.https else "disabled")
        self._compare()

    def _probe(self) -> None:
        self.probed = None
        self.fp_label.configure(text="서버 인증서를 읽는 중…", fg=theme.TEXT_SECONDARY)
        self.btn_probe.configure(state="disabled")
        self._compare()
        self.app.probe_certificate(self._probed)

    def _probed(self, fingerprint: str | None, error) -> None:
        if not self.winfo_exists():
            return
        self.btn_probe.configure(state="normal" if self.https else "disabled")
        self.probed = fingerprint
        if fingerprint:
            self.fp_label.configure(text=two_lines(fingerprint), fg=theme.TEXT_PRIMARY)
        else:
            self.fp_label.configure(text=error.message if error else "서버 인증서를 읽지 못했어요.",
                                    fg=theme.TEXT_DANGER)
        self._compare()

    def _compare(self) -> None:
        """팀 지문과 서버 지문을 비교하고, 신뢰 버튼이 무엇을 고정할지 정한다."""
        typed = self.v_team_fp.get().strip()
        team = normalize_fingerprint(typed)
        url = config.server_url(self.app.settings)
        pin = normalize_fingerprint(config.server_pin(self.app.settings, url) or "")
        if typed and team is None:
            self.compare.configure(text="SHA-256 지문 형식이 아니에요 (16진수 64자리, 콜론은 있어도 돼요).",
                                   fg=theme.TEXT_DANGER)
        elif team and self.probed:
            same = team == self.probed
            self.compare.configure(
                text="서버 지문과 같아요." if same else
                "서버 지문과 달라요. 연결을 가로채는 중일 수 있어요. 팀 지문으로 고정하면 이 서버에 보내지 않아요.",
                fg=theme.TEXT_SUCCESS if same else theme.TEXT_DANGER)
        elif team:
            self.compare.configure(text="서버 지문을 읽으면 비교해요. 팀 지문으로 바로 고정할 수도 있어요.",
                                   fg=theme.TEXT_SECONDARY)
        else:
            self.compare.configure(text="", fg=theme.TEXT_SECONDARY)
        target = team or self.probed
        allowed = self.https and not self.env_pin and target is not None and target != pin
        self.btn_trust.configure(text="팀 지문으로 고정" if team else "이 지문 신뢰",
                                 state="normal" if allowed else "disabled")
        # 앰버는 지금 할 일 하나에만: 신뢰할 지문이 있으면 신뢰, 아니면 저장
        trust_first = allowed and (team == self.probed or not team)
        self.btn_trust.set_kind("primary" if trust_first else "secondary")
        self.btn_save.set_kind("secondary" if trust_first else "primary")

    def _trust(self) -> None:
        target = normalize_fingerprint(self.v_team_fp.get()) or self.probed
        if target and self.app.trust_certificate(target):
            self.app.toast("인증서 지문을 고정했어요. 이제 이 인증서를 보내는 서버에만 연결해요.")
            self.refresh()

    def _forget(self) -> None:
        self.app.forget_certificate()
        self.app.toast("고정한 지문을 지웠어요. 시스템 인증서 저장소로 확인해요.")
        self.refresh()
