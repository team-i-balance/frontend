"""보안팀이 원문 전송 사유를 (다시) 요청하면 직원에게 보여 주고 사유를 받는다 (issue #24).

원문이 나간 결정(send_as_is · revert_original)의 서버 이벤트 번호만 설정 파일에 남긴다 (원문·값은 남기지 않음).
실행할 때와 10분마다 GET /events/{id}/reason 으로 확인하고, reason_needed면 사유 창을 띄워 POST로 낸다.
14일이 지난 번호는 지운다.
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk

from ..server.client import ServerError
from . import theme

KEEP_DAYS = 14
MAX_EVENTS = 100
EVERY_MS = 10 * 60 * 1000
RAW_ACTIONS = ("send_as_is", "revert_original")
CHOICES = (("business_need", "업무상 필요해요"), ("false_positive", "오탐이에요 (개인정보가 아님)"))


class ReasonFollowUp:
    def __init__(self, app):
        self.app = app
        self.busy = False
        self.dialog = None
        self.asked: set[int] = set()  # 이번 실행에서 이미 띄운 번호 (닫으면 다음 확인 때 다시)

    # ------------------------------------------------------------------ 기록
    def events(self) -> list[dict]:
        cutoff = time.time() - KEEP_DAYS * 86400
        kept = [e for e in self.app.settings.get("reason_events", [])
                if isinstance(e, dict) and isinstance(e.get("id"), int) and e.get("at", 0) >= cutoff]
        return kept[-MAX_EVENTS:]

    def note(self, result, action: str) -> None:
        """메인 스레드에서: 원문이 나간 결정의 이벤트 번호를 남긴다."""
        event_id = getattr(result, "event_id", None)
        if action not in RAW_ACTIONS or not isinstance(event_id, int):
            return
        events = [e for e in self.events() if e["id"] != event_id]
        events.append({"id": event_id, "at": time.time()})
        self.app.settings["reason_events"] = events[-MAX_EVENTS:]
        self.app.save_settings()
        if getattr(result, "reason_requested", False):
            self.app.root.after(1500, self.check)

    def _forget(self, event_ids) -> None:
        gone = set(event_ids)
        self.app.settings["reason_events"] = [e for e in self.events() if e["id"] not in gone]
        self.app.save_settings()

    # ------------------------------------------------------------------ 확인
    def start(self) -> None:
        self.app.root.after(5000, self._periodic)

    def _periodic(self) -> None:
        self.check()
        self.app.root.after(EVERY_MS, self._periodic)

    def check(self) -> None:
        server = self.app.server if self.app.server_mode() else None
        events = self.events()
        if server is None or not events or self.busy or self.dialog is not None:
            return
        self.busy = True

        def work():
            needed, gone = [], []
            for event in events:
                try:
                    status = server.reason_status(event["id"])
                except ServerError as exc:
                    if exc.status == 404:
                        gone.append(event["id"])  # 서버에 없는 번호 (다른 서버·지운 기록)
                        continue
                    if exc.kind in ("unreachable", "tls", "untrusted_cert", "pin_mismatch", "insecure"):
                        break  # 서버에 닿지 않음: 다음에 다시
                    continue
                except Exception:
                    continue
                if status.get("reason_needed"):
                    needed.append((event["id"], status.get("reason_requested_at") or ""))
            self.app.post("call", self._checked, needed, gone)

        threading.Thread(target=work, daemon=True, name="reason-followup").start()

    def _checked(self, needed, gone) -> None:
        self.busy = False
        if gone:
            self._forget(gone)
        fresh = [n for n in needed if n[0] not in self.asked]
        if fresh and self.dialog is None:
            event_id, requested_at = fresh[0]
            self.asked.add(event_id)
            self.dialog = ReasonRequestDialog(self, event_id, requested_at, len(needed))

    def submit(self, event_id: int, reason: str, text: str, done) -> None:
        server = self.app.server

        def work():
            try:
                if server is None:
                    raise ServerError("서버에 연결돼 있지 않아요.", "unreachable")
                server.submit_reason(event_id, reason, text)
                error = None
            except ServerError as exc:
                error = exc.message
            except Exception:
                error = "사유를 보내지 못했어요."
            self.app.post("call", done, error)

        threading.Thread(target=work, daemon=True, name="reason-submit").start()

    def closed(self, sent_id: int | None) -> None:
        self.dialog = None
        if sent_id is not None:
            self._forget([sent_id])
            self.app.root.after(500, self.check)  # 요청이 더 있으면 이어서


class ReasonRequestDialog(theme.Dialog):
    def __init__(self, followup: ReasonFollowUp, event_id: int, requested_at: str, count: int):
        when = requested_at.replace("T", " ")[:16] if requested_at else ""
        desc = (f"원문을 보낸 기록(#{event_id}{' · ' + when + ' 요청' if when else ''})에 사유가 필요해요. "
                "원문은 다시 보내지 않고 사유만 기록돼요.")
        if count > 1:
            desc += f" 요청이 {count}건 있어요."
        super().__init__(followup.app.root, "보안팀이 사유를 요청했어요", desc)
        self.followup, self.event_id = followup, event_id
        self.protocol("WM_DELETE_WINDOW", lambda: self._close(None))
        self.var = tk.StringVar(value=CHOICES[0][0])
        for code, name in CHOICES:
            ttk.Radiobutton(self.body, text=name, value=code, variable=self.var,
                            style="Dialog.TRadiobutton").pack(fill="x")
        theme.label(self.body, "설명 (1~500자)").pack(fill="x", pady=(theme.px(12), theme.px(4)))
        self.text = tk.Text(self.body, height=4, wrap="word", font=theme.F["t13"], bg=theme.WELL,
                            fg=theme.FG, insertbackground=theme.FG, relief="flat", padx=8, pady=6)
        self.text.pack(fill="x")
        self.info = theme.label(self.body, "")
        self.info.pack(fill="x", pady=(theme.px(6), 0))
        self.send = theme.Button(self.buttons, "사유 보내기", self._send, kind="primary")
        self.send.pack(side="right")
        theme.Button(self.buttons, "나중에", lambda: self._close(None)).pack(side="right", padx=theme.px(8))
        self.update_idletasks()
        self.lift()
        self.attributes("-topmost", True)
        self.after(400, lambda: self.winfo_exists() and self.attributes("-topmost", False))
        self.text.focus_set()

    def _send(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if not 1 <= len(text) <= 500:
            self.info.configure(text="설명을 1~500자로 적어 주세요.", fg=theme.DANGER)
            return
        self.send.configure(state="disabled")
        self.info.configure(text="보내는 중…", fg=theme.MUTE)

        def done(error):
            if not self.winfo_exists():
                return
            if error:
                self.send.configure(state="normal")
                self.info.configure(text=error, fg=theme.DANGER)
                return
            self.followup.app.toast("사유를 보냈어요")
            self._close(self.event_id)

        self.followup.submit(self.event_id, self.var.get(), text, done)

    def _close(self, sent_id) -> None:
        self.followup.closed(sent_id)
        self.destroy()
