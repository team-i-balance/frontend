"""설정·상태 창. 트레이가 없을 때는 이 창이 제어판 역할을 한다."""
from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

from .. import config
from ..events.log import QUEUE_LABELS
from ..system import autostart
from . import theme
from .samples import SAMPLES


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("KP Guard — 설정·상태")
        self.geometry("560x780")
        self.minsize(560, 600)
        self.resizable(False, True)
        self.configure(bg="white", padx=18, pady=14)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.status = tk.Label(self, font=theme.TITLE, bg="white", anchor="w")
        self.status.pack(fill="x")
        self.sub = tk.Label(self, font=theme.SMALL, bg="white", fg=theme.MUTED, anchor="w", justify="left",
                            wraplength=490)
        self.sub.pack(fill="x", pady=(0, 8))

        self.v_monitor = tk.BooleanVar(value=app.settings["monitoring"])
        self.v_clip = tk.BooleanVar(value=app.settings["clipboard_watch"])
        self.v_auto = tk.BooleanVar(value=autostart.is_enabled())
        box = ttk.LabelFrame(self, text=" 동작 ", padding=8)
        box.pack(fill="x", pady=4)
        ttk.Checkbutton(box, text="감시 켜기", variable=self.v_monitor, command=self._toggle_monitor).pack(anchor="w")
        ttk.Checkbutton(box, text="복사한 프롬프트 자동 점검 (클립보드)", variable=self.v_clip,
                        command=self._toggle_clip).pack(anchor="w")
        ttk.Checkbutton(box, text="Windows 시작 시 자동 실행", variable=self.v_auto,
                        command=self._toggle_auto).pack(anchor="w")
        row = tk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        self.btn_pause = theme.plain_button(row, "30분 일시정지", lambda: app.post("pause"), small=True)
        self.btn_pause.pack(side="left")

        pbox = ttk.LabelFrame(self, text=" 처리 위치 ", padding=8)
        pbox.pack(fill="x", pady=4)
        self.v_proc = tk.StringVar(value=app.settings.get("processing", "server"))
        ttk.Radiobutton(pbox, text="서버에서 처리 (탐지·가명화·기록·답변 복원)", value="server", variable=self.v_proc,
                        command=self._set_processing).pack(anchor="w")
        ttk.Radiobutton(pbox, text="이 PC에서만 처리 (서버로 보내지 않음)", value="local", variable=self.v_proc,
                        command=self._set_processing).pack(anchor="w")
        urow = tk.Frame(pbox)
        urow.pack(fill="x", pady=(6, 0))
        self.v_url = tk.StringVar(value=app.settings.get("server_url", ""))
        ttk.Entry(urow, textvariable=self.v_url).pack(side="left", fill="x", expand=True)
        theme.plain_button(urow, "저장·연결 확인", self._save_server, small=True).pack(side="left", padx=(6, 0))
        self.server_info = tk.Label(pbox, font=theme.SMALL, anchor="w", justify="left", wraplength=490)
        self.server_info.pack(fill="x", pady=(4, 0))

        act = ttk.LabelFrame(self, text=" 열기 ", padding=8)
        act.pack(fill="x", pady=4)
        theme.plain_button(act, "프롬프트 점검 창  (Ctrl+Alt+P)", lambda: app.post("review")).pack(fill="x")
        theme.plain_button(act, "답변 복원 창  (Ctrl+Alt+R)", lambda: app.post("restore")).pack(fill="x", pady=4)
        theme.plain_button(act, "파일 검사 · 엑셀 보호본 만들기  (Ctrl+Alt+F)", lambda: app.post("files")).pack(
            fill="x", pady=(0, 4))
        srow = tk.Frame(act)
        srow.pack(fill="x")
        self.sample = ttk.Combobox(srow, values=list(SAMPLES), state="readonly")
        self.sample.current(0)
        self.sample.pack(side="left", fill="x", expand=True)
        theme.plain_button(srow, "예시로 점검", lambda: app.post("sample", self.sample.get()), small=True).pack(side="left", padx=(6, 0))
        self.hotkey_info = tk.Label(act, font=theme.SMALL, fg=theme.MUTED, anchor="w", justify="left")
        self.hotkey_info.pack(fill="x", pady=(6, 0))

        mbox = ttk.LabelFrame(self, text=" 이 PC의 소형 모델 (이 PC에서 처리할 때 쓰는 2단계 탐지) ", padding=8)
        mbox.pack(fill="x", pady=4)
        self.model_info = tk.Label(mbox, font=theme.SMALL, anchor="w", justify="left", wraplength=470)
        self.model_info.pack(fill="x")
        mrow = tk.Frame(mbox)
        mrow.pack(fill="x", pady=(6, 0))
        theme.plain_button(mrow, "모델 폴더 선택…", self._pick_model, small=True).pack(side="left")
        self.btn_model = theme.plain_button(mrow, "모델 끄기", self._toggle_model, small=True)
        self.btn_model.pack(side="left", padx=6)

        logs = ttk.LabelFrame(self, text=" 이벤트 로그 (원문 없이 유형·건수·사유만) ", padding=8)
        logs.pack(fill="both", expand=True, pady=4)
        self.stats = tk.Label(logs, font=theme.FONT, anchor="nw", justify="left")
        self.stats.pack(fill="both", expand=True)
        lrow = tk.Frame(logs)
        lrow.pack(fill="x")
        theme.plain_button(lrow, "오늘 로그 JSONL 내보내기", self._export, small=True).pack(side="left")
        theme.plain_button(lrow, "로그 폴더 열기", self._open_folder, small=True).pack(side="left", padx=6)

        bottom = tk.Frame(self, bg="white")
        bottom.pack(side="bottom", fill="x", pady=(10, 0), before=logs)  # 창이 낮아도 종료 버튼은 보이게
        theme.plain_button(bottom, "매핑 초기화", self._reset_mapping, small=True).pack(side="left")
        tk.Button(bottom, text="KP Guard 종료", command=lambda: app.post("quit"), relief="flat", bg="white",
                  fg="#b91c1c", font=theme.SMALL, cursor="hand2").pack(side="right")
        self.refresh()
        self._tick()

    def refresh(self) -> None:
        state = self.app.state()
        if state == "on":
            self.status.configure(text="● 감시 중", fg="#15803d")
        elif state == "paused":
            until = datetime.fromtimestamp(self.app.paused_until).strftime("%H:%M")
            self.status.configure(text=f"● 일시정지 ({until}까지)", fg="#b45309")
        else:
            self.status.configure(text="● 꺼짐", fg=theme.MUTED)
        self.btn_pause.configure(text="일시정지 해제" if state == "paused" else "30분 일시정지")
        tray = "트레이 아이콘에서도 제어할 수 있어요." if self.app.tray else \
            "트레이 아이콘을 쓰려면 pip install pystray pillow 후 다시 실행하세요. 이 창을 닫아도 백그라운드에서 계속 돌아가요."
        server = self.app.server
        if self.app.server_mode():
            where = (f"점검 창을 열면 그 글을 팀 서버({server.base_url if server else '주소 없음'})로 보내 검사해요. "
                     "복사만 하고 창을 열지 않은 글은 보내지 않아요.")
        else:
            where = "외부 네트워크 연결 없이 이 PC 안에서만 동작합니다."
        self.sub.configure(text=where + "\n" + tray)
        self.v_monitor.set(self.app.settings["monitoring"])
        self.v_proc.set(self.app.settings.get("processing", "server"))
        lines = [f"서버 상태: {self.app.server_status}"]
        if os.environ.get("KPGUARD_SERVER_URL"):
            lines.append(f"KPGUARD_SERVER_URL 환경 변수의 주소를 쓰고 있어요: {os.environ['KPGUARD_SERVER_URL']}")
        if server is not None:
            lines.append({None: "서버 모델: 첫 검사 때 확인해요", True: "서버 모델: 사용 중 (모델+규칙)",
                          False: "서버 모델: 없음 → 규칙만으로 검사해요 (이름·주소 미검사)"}[server.model_available])
            if server.insecure:
                lines.append("암호화되지 않은 연결(HTTP)이에요. 실제 개인정보로 쓰기 전에 서버를 HTTPS로 바꿔야 해요.")
        bad = server is None or server.insecure or self.app.server_status not in ("연결됨", "확인 전", "확인 중…")
        self.server_info.configure(text="\n".join(lines), fg="#b91c1c" if bad else "black")
        models = self.app.models
        info = models.describe()
        if models.state == "off" and self.app.settings.get("model_enabled", True):
            info += f"\n모델 파일(model.onnx, tokenizer.json, config.json)이 든 폴더를 고르거나 {config.default_model_dir()}에 넣으세요."
        self.model_info.configure(text=info, fg="#b91c1c" if models.state == "error" else "black")
        self.btn_model.configure(text="모델 켜기" if not self.app.settings.get("model_enabled", True) else "모델 끄기")
        hk = self.app.hotkeys
        info = "단축키: " + (", ".join(hk.registered) or "등록 대기 중")
        if hk.failed:
            info += f"\n사용 불가(다른 프로그램이 사용 중): {', '.join(hk.failed)}"
        self.hotkey_info.configure(text=info)
        st = self.app.log.stats()
        lines = [f"{label}: {st['queues'].get(q, 0)}건" for q, label in QUEUE_LABELS.items()]
        if st["acceptance"] is not None:
            lines.append(f"\n변환 수용률: {st['acceptance'] * 100:.0f}%  (전송 {st['sent']}건 중 변환문 그대로 전송)")
        lines.append(f"매핑: {len(self.app.session.entries)}개 (메모리)")
        self.stats.configure(text="\n".join(lines))

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
            self.app.toast("서버 주소는 https://주소[:포트] 형식으로 적어 주세요.")
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
        self.app.toast("매핑을 지웠어요. 이전 답변은 더 이상 복원되지 않아요.")
        self.refresh()

    def _close(self) -> None:
        if not self.app.tray:
            self.app.toast("백그라운드에서 계속 실행 중이에요. Ctrl+Alt+O로 다시 열 수 있어요.")
        self.app.settings_closed()
        self.destroy()
