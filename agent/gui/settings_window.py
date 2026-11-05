"""설정·상태 창. 트레이가 없을 때는 이 창이 제어판 역할을 한다."""
from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from ..events.log import QUEUE_LABELS
from ..system import autostart
from . import theme
from .samples import SAMPLES


class SettingsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("KP Guard — 설정·상태")
        self.geometry("520x640")
        self.resizable(False, True)
        self.configure(bg="white", padx=18, pady=14)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.status = tk.Label(self, font=theme.TITLE, bg="white", anchor="w")
        self.status.pack(fill="x")
        self.sub = tk.Label(self, font=theme.SMALL, bg="white", fg=theme.MUTED, anchor="w", justify="left")
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

        act = ttk.LabelFrame(self, text=" 열기 ", padding=8)
        act.pack(fill="x", pady=4)
        theme.plain_button(act, "프롬프트 점검 창  (Ctrl+Alt+P)", lambda: app.post("review")).pack(fill="x")
        theme.plain_button(act, "답변 복원 창  (Ctrl+Alt+R)", lambda: app.post("restore")).pack(fill="x", pady=4)
        srow = tk.Frame(act)
        srow.pack(fill="x")
        self.sample = ttk.Combobox(srow, values=list(SAMPLES), state="readonly")
        self.sample.current(0)
        self.sample.pack(side="left", fill="x", expand=True)
        theme.plain_button(srow, "예시로 점검", lambda: app.post("sample", self.sample.get()), small=True).pack(side="left", padx=(6, 0))
        self.hotkey_info = tk.Label(act, font=theme.SMALL, fg=theme.MUTED, anchor="w", justify="left")
        self.hotkey_info.pack(fill="x", pady=(6, 0))

        logs = ttk.LabelFrame(self, text=" 이벤트 로그 (원문 없이 유형·건수·사유만) ", padding=8)
        logs.pack(fill="both", expand=True, pady=4)
        self.stats = tk.Label(logs, font=theme.FONT, anchor="nw", justify="left")
        self.stats.pack(fill="both", expand=True)
        lrow = tk.Frame(logs)
        lrow.pack(fill="x")
        theme.plain_button(lrow, "오늘 로그 JSONL 내보내기", self._export, small=True).pack(side="left")
        theme.plain_button(lrow, "로그 폴더 열기", self._open_folder, small=True).pack(side="left", padx=6)

        bottom = tk.Frame(self, bg="white")
        bottom.pack(fill="x", pady=(10, 0))
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
        self.sub.configure(text="외부 네트워크 연결 없이 이 PC 안에서만 동작합니다.\n" + tray)
        self.v_monitor.set(self.app.settings["monitoring"])
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
