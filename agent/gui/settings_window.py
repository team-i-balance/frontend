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
from ..system import autostart
from . import theme
from .samples import SAMPLES


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        theme.window(self, "KP Guard — 설정·상태", 1200, 780, 1000, 600)
        self.protocol("WM_DELETE_WINDOW", self._close)
        gap, px = theme.px(16), theme.px

        head = tk.Frame(self, bg=theme.BG_CANVAS)
        head.pack(fill="x", padx=gap, pady=(gap, 0))
        title_row = tk.Frame(head, bg=theme.BG_CANVAS)
        title_row.pack(fill="x")
        tk.Label(title_row, text="KP Guard", font=theme.F["title24"], bg=theme.BG_CANVAS,
                 fg=theme.TEXT_PRIMARY).pack(side="left")
        self.status = theme.Badge(title_row)
        self.status.pack(side="left", padx=(px(12), 0))
        self.sub = theme.label(head, "", wraplength=px(1100))
        self.sub.pack(fill="x", pady=(px(6), 0))

        bottom = tk.Frame(self, bg=theme.BG_CANVAS)
        bottom.pack(side="bottom", fill="x", padx=gap, pady=(px(8), gap))
        theme.Button(bottom, "매핑 초기화", self._reset_mapping, kind="ghost", size="sm").pack(side="left")
        theme.Button(bottom, "KP Guard 종료", lambda: app.post("quit"), kind="danger", size="sm").pack(side="right")

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
                               ("복사한 프롬프트 자동 점검", self.v_clip, self._toggle_clip),
                               ("Windows 시작 시 자동 실행", self.v_auto, self._toggle_auto)):
            theme.Switch(box.body, text, var, cmd).pack(anchor="w", pady=(0, px(12)))
        if __import__("sys").platform == "darwin":
            from ..system import mac_clipboard
            detail = "복사한 텍스트를 이 PC에서 확인합니다. macOS의 붙여넣기 허용 요청을 확인하세요."
            if not mac_clipboard.available():
                detail = "클립보드 자동 점검을 사용할 수 없어요. macOS 의존성을 설치하거나 직접 입력하세요."
            theme.label(box.body, detail, "caption12", theme.TEXT_SECONDARY, wraplength=px(300)).pack(fill="x")
        self.btn_pause = theme.Button(box.body, "30분 일시정지", lambda: app.post("pause"), size="sm")
        self.btn_pause.pack(anchor="w")

        # ---------------------------------------------------------------- 처리 위치
        pbox = theme.Card(col1, title="처리 위치", fit=True)
        pbox.pack(fill="x", pady=(px(16), 0))
        self.v_proc = tk.StringVar(value=app.settings.get("processing", "server"))
        theme.Segmented(pbox.body, [("server", "서버에서 처리"), ("local", "이 PC에서만 처리")], self.v_proc,
                        self._set_processing).pack(fill="x")
        self.v_url = tk.StringVar(value=app.settings.get("server_url", ""))
        theme.Input(pbox.body, textvariable=self.v_url).pack(fill="x", pady=(px(12), 0))
        theme.Button(pbox.body, "저장·연결 확인", self._save_server, size="sm").pack(anchor="w", pady=(px(8), 0))
        self.server_info = theme.label(pbox.body, "", wraplength=px(330))
        self.server_info.pack(fill="x", pady=(px(8), 0))

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
        self.hotkey_info = theme.label(act.body, "", "caption12", theme.TEXT_TERTIARY, wraplength=px(330))
        self.hotkey_info.pack(fill="x", pady=(px(12), 0))

        # ---------------------------------------------------------------- 소형 모델
        mbox = theme.Card(col2, title="이 PC의 소형 모델", caption="이 PC에서 처리할 때 쓰는 2단계 탐지", fit=True)
        mbox.pack(fill="x", pady=(px(16), 0))
        self.model_info = theme.label(mbox.body, "", wraplength=px(330))
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
            where = (f"점검 창을 열면 그 글을 팀 서버({server.base_url if server else '주소 없음'})로 보내 검사해요. "
                     "복사만 하고 창을 열지 않은 글은 보내지 않아요.")
        else:
            where = "외부 네트워크 연결 없이 이 PC 안에서만 동작해요."
        self.sub.configure(text=f"{where} {tray}")
        self.v_monitor.set(self.app.settings["monitoring"])
        self.v_proc.set(self.app.settings.get("processing", "server"))
        status = self.app.server_status
        lines = [f"서버: {status if status in ('연결됨', '확인 전', '확인 중…') else '연결 안 됨 · ' + status}"]
        if os.environ.get("KPGUARD_SERVER_URL"):
            lines.append(f"KPGUARD_SERVER_URL 주소 사용 중: {os.environ['KPGUARD_SERVER_URL']}")
        rules_only = False
        if server is not None and status == "연결됨":
            line, rules_only = self._coverage(server)
            lines.append(line)
        if server is not None and server.insecure:
            lines.append("암호화되지 않은 연결(HTTP) · 실제 개인정보는 HTTPS로 바꾼 뒤에")
        bad = (server is None or server.insecure or rules_only
               or self.app.server_status not in ("연결됨", "확인 전", "확인 중…"))
        self.server_info.configure(text="\n".join(lines), fg=theme.TEXT_DANGER if bad else theme.TEXT_SECONDARY)
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

    def _toggle_clip(self) -> None:
        self.app.settings["clipboard_watch"] = self.v_clip.get()
        self.app.save_settings()

    def _toggle_auto(self) -> None:
        try:
            autostart.enable() if self.v_auto.get() else autostart.disable()
        except OSError as exc:
            self.app.toast(f"자동 시작 설정 실패: {exc}")
        self.v_auto.set(autostart.is_enabled())

    def _set_processing(self) -> None:
        self.app.set_server(self.v_proc.get())
        self.refresh()

    def _save_server(self) -> None:
        self.app.set_server(self.v_proc.get(), self.v_url.get())
        if self.app.server is None:
            self.app.toast("서버 주소는 https://주소[:포트] 형식으로 적어 주세요")
        self.refresh()

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
