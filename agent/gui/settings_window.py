"""설정·상태 창. 트레이가 없을 때는 이 창이 제어판 역할을 한다.

배치(디자인 설정): 세 열의 카드. 즉시 적용되는 켜기·끄기는 스위치, 두 갈래 선택(처리 위치)은 세그먼트.
이 창에는 '지금 처리할 한 곳'이 없어서 앰버 주요 버튼을 두지 않는다. 상태는 배지 문구와 톤으로 함께 알린다.
"""
from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog

from .. import config
from ..events.log import QUEUE_LABELS
from ..server.client import AUTH_KINDS, CERT_KINDS
from ..system import autostart
from . import theme
from .samples import SAMPLES
from .server_auth import ServerAuthDialog, short_fingerprint

# 연결·인증 오류는 이 창에서 짧게 (자세한 안내는 '인증·인증서' 대화상자에서)
SHORT_STATUS = {
    "untrusted_cert": "인증서 확인 필요 · '인증·인증서'에서 지문을 확인하세요",
    "pin_mismatch": "인증서가 신뢰한 것과 달라요 · 가로채기일 수 있어 아무것도 보내지 않았어요",
    "unauthorized": "토큰이 맞지 않아요 · '인증·인증서'에서 확인하세요",
    "forbidden": "토큰 권한이 없어요 · 개발용 토큰을 막은 서버면 팀 토큰이 필요해요",
    "insecure": "HTTP 주소에는 토큰을 보내지 않아요 · https://로 바꾸세요",
    "tls": "암호화 연결(TLS) 실패 · 주소의 https와 포트를 확인하세요",
}


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        theme.window(self, f"설정·상태 — {theme.APP_TITLE}", 1200, 780, 1000, 600)
        self.protocol("WM_DELETE_WINDOW", self._close)
        gap, px = theme.px(24), theme.px

        # 화면 머리: 로고 마크(22) + 이름 22/600 + 상태 점, 아래 회색 메타 한 줄
        head = tk.Frame(self, bg=theme.BG_CANVAS)
        head.pack(fill="x", padx=gap, pady=(gap, 0))
        title_row = tk.Frame(head, bg=theme.BG_CANVAS)
        title_row.pack(fill="x")
        mark = tk.Canvas(title_row, width=px(22), height=px(22), highlightthickness=0, bd=0, bg=theme.BG_CANVAS)
        theme.draw_mark(mark, 0, 0, px(22))
        mark.pack(side="left", padx=(0, px(8)))
        tk.Label(title_row, text=theme.APP_TITLE, font=theme.F["t22s"], bg=theme.BG_CANVAS,
                 fg=theme.TEXT_PRIMARY).pack(side="left")
        self.status = theme.StatusDot(title_row)
        self.status.pack(side="left", padx=(px(16), 0), pady=(px(4), 0))
        self.sub = theme.label(head, "", wraplength=px(1100))
        self.sub.pack(fill="x", pady=(px(4), 0))

        bottom = tk.Frame(self, bg=theme.BG_CANVAS)
        bottom.pack(side="bottom", fill="x", padx=gap, pady=(px(8), gap))
        theme.Button(bottom, "매핑 초기화", self._reset_mapping, kind="ghost", size="sm").pack(side="left")
        theme.Button(bottom, f"{theme.APP_TITLE} 종료", lambda: app.post("quit"), kind="danger", size="sm").pack(side="right")

        cols = tk.Frame(self, bg=theme.BG_CANVAS)
        cols.pack(fill="both", expand=True, padx=gap, pady=(px(16), 0))
        col1, col2, col3 = (tk.Frame(cols, bg=theme.BG_CANVAS) for _ in range(3))
        for i, col in enumerate((col1, col2, col3)):
            col.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else px(8), 0 if i == 2 else px(8)))
            cols.columnconfigure(i, weight=1, uniform="col")
        cols.rowconfigure(0, weight=1)

        # ---------------------------------------------------------------- 동작
        box = theme.Card(col1, title="동작", fit=True)
        box.pack(fill="x")
        self.v_monitor = tk.BooleanVar(value=app.settings["monitoring"])
        self.v_clip = tk.BooleanVar(value=app.settings["clipboard_watch"])
        self.v_auto = tk.BooleanVar(value=autostart.is_enabled())
        for text, var, cmd in (("감시", self.v_monitor, self._toggle_monitor),
                               ("복사한 프롬프트 자동 점검 (점검 창이 열려요)", self.v_clip, self._toggle_clip),
                               ("Windows 시작 시 자동 실행", self.v_auto, self._toggle_auto)):
            theme.Switch(box.body, text, var, cmd).pack(anchor="w", pady=(0, px(12)))
        if __import__("sys").platform == "darwin":
            from ..system import mac_clipboard
            detail = "복사한 텍스트를 이 PC에서 확인합니다. macOS의 붙여넣기 허용 요청을 확인하세요."
            if not mac_clipboard.available():
                detail = "클립보드 자동 점검을 사용할 수 없어요. macOS 의존성을 설치하거나 직접 입력하세요."
            theme.label(box.body, detail, "caption12", theme.TEXT_SECONDARY, wraplength=px(300)).pack(fill="x")
        self.v_overlay = tk.BooleanVar(value=app.settings.get("native_overlay", True))
        theme.Switch(box.body, "AI 입력창에 자동 하이라이트", self.v_overlay, self._toggle_overlay).pack(anchor="w")
        self.overlay_status = theme.label(box.body, "", "caption12", theme.TEXT_SECONDARY)
        self.overlay_status.pack(anchor="w", pady=(0, px(6)))
        if __import__("sys").platform == "darwin":
            theme.Button(box.body, "손쉬운 사용 권한 확인", self._overlay_access, size="sm").pack(anchor="w")
        self.btn_pause = theme.Button(box.body, "30분 일시정지", lambda: app.post("pause"), size="sm")
        self.btn_pause.pack(anchor="w")
        connection = tk.Frame(box.body, bg=theme.BG_SURFACE)
        connection.pack(anchor="w", pady=(px(8), 0))
        theme.Button(connection, "브라우저 연결 코드 복사", app.pair_browser, size="sm").pack(side="left")
        theme.Button(connection, "연결 해제", app.disconnect_browser, kind="ghost", size="sm").pack(side="left")

        # ---------------------------------------------------------------- 처리 위치
        pbox = theme.Card(col1, title="처리 위치", fit=True)
        pbox.pack(fill="x", pady=(px(16), 0))
        self.v_proc = tk.StringVar(value=app.settings.get("processing", "server"))
        theme.Segmented(pbox.body, [("server", "서버에서 처리"), ("local", "이 PC에서만 처리")], self.v_proc,
                        self._set_processing).pack(fill="x")
        self.v_url = tk.StringVar(value=app.settings.get("server_url", ""))
        theme.Input(pbox.body, textvariable=self.v_url).pack(fill="x", pady=(px(12), 0))
        brow = tk.Frame(pbox.body, bg=theme.BG_SURFACE)
        brow.pack(fill="x", pady=(px(8), 0))
        theme.Button(brow, "저장·연결 확인", self._save_server, size="sm").pack(side="left")
        self.btn_auth = theme.Button(brow, "인증·인증서…", self._open_auth, kind="ghost", size="sm")
        self.btn_auth.pack(side="left", padx=(px(8), 0))
        self.server_info = theme.label(pbox.body, "", wraplength=px(300))
        self.server_info.pack(fill="x", pady=(px(8), 0))

        # ---------------------------------------------------------------- 직원 정보 (서버 기록에 함께)
        who = theme.Card(col1, title="직원 정보", caption="원문을 보낼 때 보안팀 기록에 함께 남아요", fit=True)
        who.pack(fill="x", pady=(px(16), 0))
        self.v_user = tk.StringVar(value=app.settings.get("user_id", ""))
        self.v_dept = tk.StringVar(value=app.settings.get("dept", ""))
        for caption, var in (("사번 또는 이름", self.v_user), ("부서", self.v_dept)):
            theme.label(who.body, caption).pack(fill="x", pady=(0, px(4)))
            theme.Input(who.body, textvariable=var).pack(fill="x", pady=(0, px(8)))
        theme.Button(who.body, "저장", self._save_person, size="sm").pack(anchor="w")

        # ---------------------------------------------------------------- 열기
        act = theme.Card(col2, title="열기", fit=True)
        act.pack(fill="x")
        orow = tk.Frame(act.body, bg=theme.BG_SURFACE)
        orow.pack(fill="x")
        for text, cmd in (("프롬프트 점검", "review"), ("답변 복원", "restore"), ("파일 검사", "files")):
            theme.Button(orow, text, lambda c=cmd: app.post(c), size="sm").pack(side="left", padx=(0, px(8)))
        srow = tk.Frame(act.body, bg=theme.BG_SURFACE)
        srow.pack(fill="x", pady=(px(12), 0))
        theme.Button(srow, "예시로 점검", lambda: app.post("sample", self.sample.get()), size="sm").pack(
            side="right", padx=(px(8), 0))
        self.sample = theme.Select(srow, list(SAMPLES), size="sm")
        self.sample.pack(side="left", fill="x", expand=True)
        self.hotkey_info = theme.label(act.body, "", "caption12", theme.TEXT_TERTIARY, wraplength=px(300))
        self.hotkey_info.pack(fill="x", pady=(px(12), 0))

        # ---------------------------------------------------------------- 소형 모델
        mbox = theme.Card(col2, title="이 PC의 소형 모델", caption="이 PC에서 처리할 때 쓰는 2단계 탐지", fit=True)
        mbox.pack(fill="x", pady=(px(16), 0))
        self.model_info = theme.label(mbox.body, "", wraplength=px(300))
        self.model_info.pack(fill="x")
        mrow = tk.Frame(mbox.body, bg=theme.BG_SURFACE)
        mrow.pack(fill="x", pady=(px(12), 0))
        theme.Button(mrow, "모델 폴더 선택", self._pick_model, size="sm").pack(side="left")
        self.btn_model = theme.Button(mrow, "모델 끄기", self._toggle_model, kind="ghost", size="sm")
        self.btn_model.pack(side="left", padx=(px(8), 0))

        # ---------------------------------------------------------------- 이벤트 로그
        logs = theme.Card(col3, title="이벤트 로그", caption="원문 없이 유형·건수·사유만 남겨요")
        logs.pack(fill="both", expand=True)
        lrow = tk.Frame(logs.body, bg=theme.BG_SURFACE)
        lrow.pack(side="bottom", fill="x", pady=(px(12), 0))
        theme.Button(lrow, "오늘 로그 내보내기", self._export, size="sm").pack(side="left")
        theme.Button(lrow, "폴더 열기", self._open_folder, kind="ghost", size="sm").pack(side="left", padx=(px(8), 0))
        self.stats = theme.label(logs.body, "", "body16r", theme.TEXT_PRIMARY)
        self.stats.configure(anchor="nw")
        self.stats.pack(fill="both", expand=True)
        self.refresh()
        self._tick()

    def refresh(self) -> None:
        state = self.app.state()
        if state == "on":
            self.status.set("감시 중", "success")
        elif state == "paused":
            until = datetime.fromtimestamp(self.app.paused_until).strftime("%H:%M")
            self.status.set(f"일시정지 · {until}까지", "warning")
        else:
            self.status.set("꺼짐", "neutral")
        self.btn_pause.configure(text="일시정지 해제" if state == "paused" else "30분 일시정지")
        tray = "트레이 아이콘에서도 제어할 수 있어요." if self.app.tray else \
            "트레이 아이콘을 쓰려면 pip install pystray pillow 후 다시 실행하세요. 이 창을 닫아도 백그라운드에서 계속 돌아가요."
        server = self.app.server
        if self.app.server_mode():
            where = f"자동 하이라이트를 켜면 입력 중인 AI 프롬프트를 팀 서버({server.base_url if server else '주소 없음'})로 보내 검사합니다."
        else:
            where = "외부 네트워크 연결 없이 이 PC 안에서만 동작해요."
        self.sub.configure(text=f"{where} {tray}")
        self.v_monitor.set(self.app.settings["monitoring"])
        self.overlay_status.configure(text=self.app.overlay.status)
        self.v_proc.set(self.app.settings.get("processing", "server"))
        status = self.app.server_status
        error = self.app.server_error
        short = SHORT_STATUS.get(error.kind) if error is not None else None
        lines = [f"서버: {status if status in ('연결됨', '확인 전', '확인 중…') else '연결 안 됨 · ' + (short or status)}"]
        if os.environ.get("KPGUARD_SERVER_URL"):
            lines.append(f"KPGUARD_SERVER_URL 주소 사용 중: {os.environ['KPGUARD_SERVER_URL']}")
        rules_only = False
        if server is not None and status == "연결됨":
            line, rules_only = self._coverage(server)
            lines.append(line)
        if server is not None:
            lines += self._security(server)
        if server is not None and server.insecure:
            lines.append("암호화되지 않은 연결(HTTP) · 실제 개인정보는 HTTPS로 바꾼 뒤에")
        bad = (server is None or server.insecure or rules_only
               or self.app.server_status not in ("연결됨", "확인 전", "확인 중…"))
        self.server_info.configure(text="\n".join(lines), fg=theme.TEXT_DANGER if bad else theme.TEXT_SECONDARY)
        # 인증서·토큰 문제를 고칠 곳이 지금 할 일 하나다 (앰버)
        self.btn_auth.set_kind("primary" if error is not None and error.kind in CERT_KINDS | AUTH_KINDS
                               else "ghost")
        models = self.app.models
        info = models.describe()
        if models.state == "off" and self.app.settings.get("model_enabled", True):
            info += f"\n모델 파일(model.onnx, tokenizer.json, config.json)이 든 폴더를 고르거나 {config.default_model_dir()}에 넣으세요."
        self.model_info.configure(text=info, fg=theme.TEXT_DANGER if models.state == "error" else theme.TEXT_SECONDARY)
        self.btn_model.configure(text="모델 켜기" if not self.app.settings.get("model_enabled", True) else "모델 끄기")
        hk = self.app.hotkeys
        info = "단축키: Ctrl+Alt+P 점검 · Ctrl+Alt+R 복원 · Ctrl+Alt+F 파일 · Ctrl+Alt+O 설정"
        if __import__("sys").platform == "darwin":
            info = info.replace("Ctrl+Alt", "⌘+⌥")
        if not hk.registered and not hk.failed:
            info += " (등록 대기 중)"
        if hk.failed:
            info += f"\n단축키를 사용할 수 없어요: {', '.join(hk.failed)}. macOS 입력 모니터링 권한을 확인하세요."
        self.hotkey_info.configure(text=info)
        st = self.app.log.stats()
        lines = [f"{label}  {st['queues'].get(q, 0)}건" for q, label in QUEUE_LABELS.items()]
        if st["acceptance"] is not None:
            lines.append(f"\n변환 수용률  {st['acceptance'] * 100:.0f}%  (전송 {st['sent']}건 중 변환문 그대로 전송)")
        lines.append(f"매핑  {len(self.app.session.entries)}개 (메모리)")
        self.stats.configure(text="\n".join(lines))

    @staticmethod
    def _security(server) -> list[str]:
        """인증 방식과 인증서 확인 방식 한 줄씩."""
        token = {"team": "팀 토큰", "dev": "개발용 토큰 (서버가 발급)", "none": "토큰 없음"}[server.auth_mode]
        if server.auth_mode == "team" and os.environ.get("KPGUARD_API_TOKEN"):
            token += " · KPGUARD_API_TOKEN"
        lines = [f"인증: {token}"]
        if server.cert_mode == "pinned":
            lines.append(f"인증서: 지문 고정 {short_fingerprint(server.pin)}")
        elif server.cert_mode == "system":
            lines.append("인증서: 시스템 인증서 저장소로 확인")
        return lines

    @staticmethod
    def _coverage(server) -> tuple[str, bool]:
        """서버 탐지 범위 한 줄과, 규칙만인지. 서버 /health가 알려 주면 그것을, 아니면 첫 검사 결과를 쓴다."""
        health = server.last_health
        model = health.model if health and health.model is not None else server.model_available
        if model is None:
            return "서버 탐지: 첫 검사 때 확인해요", False
        if not model:
            return "서버 탐지: 규칙만 (이름·주소·파일 검사 안 됨)", True
        if health and health.slm:
            return "서버 탐지: 모델+규칙 · SLM " + ("준비됨" if health.slm_ready else "준비 중"), False
        return "서버 탐지: 모델+규칙", False

    def _tick(self) -> None:
        if self.winfo_exists():
            self.refresh()
            self.after(3000, self._tick)

    def _toggle_monitor(self) -> None:
        if self.v_monitor.get() != self.app.settings["monitoring"]:
            self.app.post("toggle")

    def _toggle_overlay(self) -> None:
        self.app.settings["native_overlay"] = self.v_overlay.get()
        self.app.save_settings()
        self.refresh()

    def _overlay_access(self):
        try:
            from ..overlay.macos import request_access
            request_access()
        except ImportError:
            self.app.toast("화면 연동 모듈을 설치한 후 다시 실행해 주세요.", tone="warning")

    def _toggle_clip(self) -> None:
        self.app.settings["clipboard_watch"] = self.v_clip.get()
        self.app.save_settings()

    def _toggle_auto(self) -> None:
        try:
            autostart.enable() if self.v_auto.get() else autostart.disable()
        except OSError as exc:
            self.app.toast(f"자동 시작을 바꾸지 못했어요: {exc}", tone="error")
        self.v_auto.set(autostart.is_enabled())

    def _set_processing(self) -> None:
        self.app.set_server(self.v_proc.get())
        self.refresh()

    def _save_server(self) -> None:
        self.app.set_server(self.v_proc.get(), self.v_url.get())
        if self.app.server is None:
            self.app.toast("서버 주소는 https://주소[:포트] 형식으로 적어 주세요", tone="warning")
        self.refresh()

    def _save_person(self) -> None:
        self.app.settings["user_id"] = self.v_user.get().strip()[:64]
        self.app.settings["dept"] = self.v_dept.get().strip()[:128]
        self.app.save_settings()
        self.app.toast("직원 정보를 저장했어요")

    def _open_auth(self) -> None:
        dialog = getattr(self, "auth_dialog", None)
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self.auth_dialog = ServerAuthDialog(self.app, self)

    def _pick_model(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="AEGIS 모델 폴더 (model.onnx · tokenizer.json · config.json)")
        if folder:
            self.app.set_model(folder, enabled=True)
            self.refresh()

    def _toggle_model(self) -> None:
        self.app.set_model(enabled=not self.app.settings.get("model_enabled", True))
        self.refresh()

    def _export(self) -> None:
        path = self.app.log.export_jsonl()
        self.app.toast(f"내보냈어요: {path}")

    def _open_folder(self) -> None:
        os.startfile(self.app.log.folder)  # noqa: S606 (로컬 폴더 열기)

    def _reset_mapping(self) -> None:
        self.app.session.reset()
        self.app.toast("매핑을 지웠어요. 이전 답변은 더 이상 복원되지 않아요")
        self.refresh()

    def _close(self) -> None:
        if not self.app.tray:
            self.app.toast("백그라운드에서 계속 실행 중이에요. Ctrl+Alt+O로 다시 열 수 있어요")
        self.app.settings_closed()
        self.destroy()
