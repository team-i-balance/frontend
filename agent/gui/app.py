"""백그라운드 상주 앱: 트레이·단축키·클립보드 감시 → 점검 창/답변 복원 창.

스레드 규칙: tkinter는 메인 스레드에서만 다룬다. 트레이·단축키 스레드는 post()로 큐에 넣기만 한다.
"""
from __future__ import annotations

import queue
import secrets
import sys
import os
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox

from ..browser.actions import BrowserActions
from ..browser.bridge import BrowserBridge
from ..overlay.controller import NativeOverlay
from .. import config
from ..detect.model import ModelManager
from ..detect.pipeline import detect
from ..detect.scoring import AUTO_APPLY
from ..events.log import EventLog
from ..sanitize.engine import Session
from ..sanitize.restore import restore
from ..server.client import (CERT_KINDS, WORK_TTL_SECONDS, ServerClient, ServerError, normalize_fingerprint,
                             server_key, valid_token)
from ..system import autostart, platform_api
from . import theme
if sys.platform == "darwin":
    from . import mac_tray as tray
else:
    from . import tray
from .file_window import FileWindow
from .restore_window import RestoreWindow
from .reason_followup import ReasonFollowUp
from .review import ReviewWindow
from .samples import SAMPLES
from .server_review import ServerRestoreWindow, ServerReviewWindow
from .settings_window import SettingsWindow

HOTKEYS = {
    1: (platform_api.MOD_CONTROL | platform_api.MOD_ALT, ord("P"), "Ctrl+Alt+P 점검 창"),
    2: (platform_api.MOD_CONTROL | platform_api.MOD_ALT, ord("R"), "Ctrl+Alt+R 답변 복원"),
    3: (platform_api.MOD_CONTROL | platform_api.MOD_ALT, ord("O"), "Ctrl+Alt+O 설정"),
    4: (platform_api.MOD_CONTROL | platform_api.MOD_ALT, ord("F"), "Ctrl+Alt+F 파일 검사"),
}
if sys.platform == "darwin":
    HOTKEYS = {key: (mods, code, label.replace("Ctrl+Alt", "⌘+⌥")) for key, (mods, code, label) in HOTKEYS.items()}

PAUSE_SECONDS = 30 * 60
MAX_CLIP_LEN = 50_000
# 서버에서 검사한 파일의 자리표시를 기억하는 시간 (값은 없이 [이름_1] 같은 글자만).
# 프롬프트 복원에서 같은 자리표시가 나오면 파일 쪽 값일 수 있다고 알려 준다.
FILE_TOKEN_SECONDS = 60 * 60
# 복사할 때마다 도는 자동 점검은 짧은 글에만 모델을 쓴다 (메인 스레드를 오래 잡지 않게).
# 긴 글은 규칙·사전으로 창을 띄울지 정하고, 창을 직접 열면 모델로 다시 검사한다.
CLIP_MODEL_MAX_CHARS = 2000


@dataclass
class ServerWork:
    """마지막으로 보낸 변환문의 서버 작업. 서버는 /process 시점부터 10분 동안만 보관하고, 복원은 한 번만 된다."""
    work_id: str
    tokens: frozenset
    processed_at: float

    def expires_in(self) -> float:
        return WORK_TTL_SECONDS - (time.time() - self.processed_at)

    def alive(self) -> bool:
        return self.expires_in() > 0


class App:
    def __init__(self, background: bool = False):
        theme.load_fonts()  # 동봉한 Pretendard (Tk를 만들기 전에)
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(theme.APP_TITLE)
        theme.setup(self.root)

        self.settings = config.load_settings()
        self.base = config.load_base()
        self.session = Session(self.base)
        self.ignore: set[str] = set()   # 오탐 등록 값 (메모리)
        self.log = EventLog(config.data_dir())
        self.paused_until = 0.0
        self._q: queue.Queue = queue.Queue()
        self.reviews: list[ReviewWindow] = []
        self.restore_win: RestoreWindow | None = None
        self.settings_win: SettingsWindow | None = None
        self.file_win: FileWindow | None = None
        self._last_seq = platform_api.clipboard_sequence()
        self._own_seq = None
        self._toast_win = None

        # 2단계 소형 모델: 백그라운드에서 불러오고, 준비되기 전에는 규칙·사전만 쓴다
        self.models = ModelManager(on_change=lambda: self.post("model_state"))
        self._announce_model = False
        self.models.configure(config.model_dir(self.settings), self.settings["model_threads"])

        # 서버 처리: 탐지·가명화·기록·복원은 팀 서버(backend /process)가 한다
        self.server: ServerClient | None = None
        self.server_work: ServerWork | None = None
        self.file_tokens: dict[str, float] = {}   # 서버 파일 보호본의 자리표시 → 받은 시각
        self.server_status = "확인 전"
        self.server_error: ServerError | None = None  # 마지막 연결 확인 오류 (인증서·토큰 안내에 씀)
        self._setup_server()
        if self.server_mode():
            self.check_server()

        self.browser = None
        self.browser_actions = BrowserActions(self)
        self.overlay = NativeOverlay(self)
        self.reasons = ReasonFollowUp(self)  # 보안팀의 사유 (재)요청 (#24)
        self.reasons.start()
        if self.settings.get("browser_key"):
            self.start_browser()
        self.tray = tray.Tray(self) if tray.available() else None
        self.hotkeys = platform_api.HotkeyThread(HOTKEYS, lambda hid: self.post("hotkey", hid))
        self.hotkeys.start()
        if self.tray:
            self.tray.start()

        self.root.after(100, self._poll)
        self.root.after(30_000, self._expire_browser_work)
        self.root.after(700, self._watch_clipboard)
        self.root.after(400, lambda: self._first_run(background))

    def _expire_browser_work(self):
        self.browser_actions.expire()
        self.root.after(30_000, self._expire_browser_work)

    def run(self) -> None:
        self.root.mainloop()

    # ================================================================== 상태
    def is_paused(self) -> bool:
        return time.time() < self.paused_until

    def state(self) -> str:
        if not self.settings["monitoring"]:
            return "off"
        return "paused" if self.is_paused() else "on"

    def monitoring_active(self) -> bool:
        return self.state() == "on"

    def save_settings(self) -> None:
        config.save_settings(self.settings)

    def start_browser(self):
        if self.browser is None:
            try:
                self.browser = BrowserBridge(self.settings["browser_key"], self.browser_actions)
                self.browser.start()
            except OSError:
                self.browser = None
                self.toast(f"브라우저 연결 포트를 열 수 없어요. 이미 실행 중인 {theme.APP_TITLE}가 있는지 확인하세요.", tone="error")
        return self.browser is not None

    def pair_browser(self):
        if not self.settings.get("browser_key"):
            self.settings["browser_key"] = secrets.token_urlsafe(32)
            self.save_settings()
        if self.start_browser():
            self.copy_to_clipboard(self.settings["browser_key"])
            self.toast(f"연결 코드를 복사했어요. 브라우저의 {theme.APP_TITLE} 확장에 붙여넣으세요.")

    def disconnect_browser(self):
        self.settings.pop("browser_key", None)
        self.save_settings()
        if self.browser:
            self.browser.stop()
            self.browser = None
        self.toast("브라우저 연결을 해제했어요.")

    def detector(self):
        """지금 쓸 수 있는 2단계 모델 (없으면 None → 규칙·사전만)."""
        return self.models.detector()

    # ================================================================== 서버 처리
    def _setup_server(self) -> None:
        url = config.server_url(self.settings)
        self.server, self.server_work, self.server_error = None, None, None
        problem = ""
        try:
            if url:
                self.server = ServerClient(url, float(self.settings["server_timeout"]),
                                           token=config.api_token(self.settings) or None,
                                           dev_token=config.auth_mode(self.settings) == "dev",
                                           pin=config.server_pin(self.settings, url) or None)
        except ValueError as exc:
            problem = str(exc)
        if self.server is not None:
            self.server_status = "확인 전"
        elif problem.startswith("token"):
            self.server_status = "팀 토큰 형식이 올바르지 않아요"
        elif problem.startswith("pin"):
            self.server_status = "고정한 인증서 지문 형식이 올바르지 않아요"
        else:
            self.server_status = "서버 주소가 올바르지 않아요"

    def server_mode(self) -> bool:
        return self.settings.get("processing") == "server"

    def set_server(self, processing: str, url: str | None = None) -> None:
        """설정 창에서 처리 위치나 서버 주소를 바꿀 때."""
        self.settings["processing"] = processing
        if url is not None:
            self.settings["server_url"] = url.strip()
        self._reconnect()

    def _reconnect(self) -> None:
        self.save_settings()
        self._setup_server()
        self._state_changed()
        if self.server_mode():
            self.check_server()

    def set_auth(self, mode: str, token: str | None = None) -> str | None:
        """인증 방식(dev / team)과 팀 토큰을 바꾼다. 토큰 형식이 틀리면 저장하지 않고 이유를 돌려준다."""
        if mode == "team" and token is not None:
            token = token.strip()
            if token and not valid_token(token):
                return "토큰에는 영문·숫자와 - . _ ~ + / = 만 쓸 수 있어요."
            self.settings["api_token"] = token
        self.settings["auth_mode"] = "team" if mode == "team" else "dev"
        self._reconnect()
        return None

    def trust_certificate(self, fingerprint: str) -> bool:
        """지금 서버 주소(호스트:포트)에 인증서 지문을 고정한다. 다음 연결부터 이 인증서만 믿는다."""
        value = normalize_fingerprint(fingerprint)
        url = config.server_url(self.settings)
        if value is None or not url:
            return False
        self.settings["server_pins"] = {**self.settings.get("server_pins", {}), server_key(url): value}
        self._reconnect()
        return True

    def forget_certificate(self) -> None:
        """고정한 지문을 지운다. 다음 연결부터 시스템 인증서 저장소로 확인한다."""
        url = config.server_url(self.settings)
        pins = dict(self.settings.get("server_pins", {}))
        pins.pop(server_key(url), None)
        self.settings["server_pins"] = pins
        self._reconnect()

    def probe_certificate(self, done) -> None:
        """서버가 내보이는 인증서 지문을 읽는다 (작업 스레드, 토큰·글은 보내지 않음).
        done(지문 또는 None, 오류 또는 None)은 메인 스레드에서 부른다."""
        server = self.server
        if server is None:
            done(None, ServerError("서버 주소가 올바르지 않아요.", "unreachable"))
            return

        def work():
            try:
                fingerprint, error = server.probe_certificate(), None
            except ServerError as exc:
                fingerprint, error = None, exc
            except Exception:  # 예외 메시지는 보여 주지 않는다
                fingerprint, error = None, ServerError("서버 인증서를 읽지 못했어요.", "tls")
            self.post("call", done, fingerprint, error)

        threading.Thread(target=work, daemon=True, name="server-cert").start()

    def check_server(self) -> None:
        """연결 확인 (작업 스레드). 인증서와 토큰까지 확인하고, 원문은 보내지 않는다."""
        server = self.server
        if server is None:
            self.post("server_health", self.server_status, None)
            return
        self.server_status = "확인 중…"

        def work():
            error = None
            try:  # 탐지 범위(모델+규칙 / 규칙만, SLM 준비)도 함께 받아 둔다 → server.last_health
                if server.health().ok:
                    server.check_auth()  # 토큰 확인. 개발용 토큰은 여기서 받는다
                    status = "연결됨"
                else:
                    status = "응답이 이상해요"
            except ServerError as exc:
                status, error = exc.message, exc
            self.post("server_health", status, error, server)

        threading.Thread(target=work, daemon=True, name="server-health").start()

    def decide_context(self, target_ai: str | None = None) -> dict:
        """/process/decide에 함께 보내는 직원·대상 AI (backend#63). 비어 있으면 보내지 않는다."""
        context = {"user_id": os.environ.get("KPGUARD_USER_ID") or self.settings.get("user_id", ""),
                   "dept": os.environ.get("KPGUARD_DEPT") or self.settings.get("dept", "")}
        if target_ai:
            context["target_ai"] = target_ai
        return context

    def server_decide(self, result, action: str, reason: str | None = None, retry_text: str | None = None,
                      tokens=frozenset(), reverted=None, target_ai: str | None = None) -> None:
        """사용자 선택을 서버에 기록한다 (작업 스레드). 작업이 만료됐으면 같은 글로 한 번 다시 검사해 기록한다.

        reverted: revert_original에서 원문으로 되돌린 서버 자리표시. 다시 검사하면 자리표시가 달라질 수 있어
        그때는 목록 없이(전부 되돌림으로) 기록한다 — 보안팀에 덜 보이는 쪽보다 더 보이는 쪽을 택한다."""
        server = self.server
        if server is None or result is None:
            return
        context = self.decide_context(target_ai)

        def work():
            work_id, processed_at = result.work_id, result.received_at
            try:
                try:
                    out = server.decide(work_id, action, reason, reverted=reverted, context=context)
                except ServerError as exc:
                    if exc.kind != "expired" or retry_text is None:
                        raise
                    again = server.process(retry_text)
                    work_id, processed_at = again.work_id, again.received_at
                    out = server.decide(work_id, action, reason, context=context)
            except ServerError as exc:
                if action != "cancel":
                    self.post("server_decided", None, exc.message)
                return
            self.post("call", self.reasons.note, out, action)
            if out.restore_available and tokens:
                self.post("server_decided", ServerWork(work_id, frozenset(tokens), processed_at), None)

        threading.Thread(target=work, daemon=True, name="server-decide").start()

    def note_file_tokens(self, tokens) -> None:
        now = time.time()
        for token in tokens:
            self.file_tokens[token] = now

    def recent_file_tokens(self) -> set[str]:
        """최근 서버에서 검사한 파일 보호본에 들어간 자리표시. 서버에 파일 복원 기능이 없어 복원은 못 한다."""
        cutoff = time.time() - FILE_TOKEN_SECONDS
        self.file_tokens = {t: at for t, at in self.file_tokens.items() if at >= cutoff}
        return set(self.file_tokens)

    def set_model(self, folder: str | None = None, enabled: bool = True) -> None:
        """설정 창에서 모델 폴더를 고르거나 끌 때."""
        if folder is not None:
            self.settings["model_dir"] = folder
        self.settings["model_enabled"] = enabled
        self.save_settings()
        self._announce_model = True
        self.models.configure(config.model_dir(self.settings), self.settings["model_threads"])

    def _state_changed(self) -> None:
        if self.tray:
            self.tray.refresh()
        if self.settings_win:
            self.settings_win.refresh()

    # ================================================================== 큐 (다른 스레드 → 메인)
    def post(self, cmd: str, *args) -> None:
        self._q.put((cmd, args))

    def _poll(self) -> None:
        try:
            while True:
                cmd, args = self._q.get_nowait()
                self._dispatch(cmd, *args)
        except queue.Empty:
            pass
        if self.is_paused() is False and self.paused_until:
            self.paused_until = 0.0
            self._state_changed()
        self.root.after(100, self._poll)

    def _dispatch(self, cmd: str, *args) -> None:
        if cmd == "server_tools":
            if self.server_mode() and self.server:
                from .server_tools import ServerToolsWindow
                ServerToolsWindow(self)
            else:
                self.open_settings()
        elif cmd == "review":
            self.open_review("", "manual")
        elif cmd == "restore":
            self.open_restore()
        elif cmd == "settings":
            self.open_settings()
        elif cmd == "sample":
            self.open_review(SAMPLES[args[0]], "sample")
        elif cmd == "toggle":
            self.settings["monitoring"] = not self.settings["monitoring"]
            self.save_settings()
            self.toast("감시를 켰어요." if self.settings["monitoring"] else "감시를 껐어요. 복사한 내용을 점검하지 않아요.")
            self._state_changed()
        elif cmd == "pause":
            if self.is_paused():
                self.paused_until = 0.0
                self.toast("일시정지를 해제했어요.")
            else:
                self.paused_until = time.time() + PAUSE_SECONDS
                self.toast("30분 동안 감시를 멈춰요.")
            self._state_changed()
        elif cmd == "files":
            self.open_files()
        elif cmd == "hotkey":
            {1: lambda: self.open_review("", "hotkey"), 2: self.open_restore, 3: self.open_settings,
             4: self.open_files}[args[0]]()
        elif cmd == "model_state":
            self._model_state_changed()
        elif cmd == "server_health":
            status, error, *checked = args
            if checked and checked[0] is not self.server:
                return  # 그 사이 설정을 바꿔 다른 서버로 다시 확인 중
            self.server_status, self.server_error = status, error
            if error is not None and error.kind in CERT_KINDS and not self.settings_win:
                self.toast("서버 인증서를 확인해야 해요. 설정 창(Ctrl+Alt+O)의 '인증·인증서'에서 확인하세요.", tone="warning")
            self._state_changed()
        elif cmd == "call":
            args[0](*args[1:])
        elif cmd == "chip_restore":
            # 칩으로 가린 입력을 기록했다: 답변 복원 창·복사 감지가 이 서버 작업으로 복원한다
            result, tokens = args
            self.server_work = ServerWork(result.work_id, frozenset(tokens), result.received_at)
        elif cmd == "server_decided":
            work, error = args
            if work is not None:
                self.server_work = work
            if error:
                self.toast(f"서버 기록에 실패했어요. {error}", tone="error")
        elif cmd == "quit":
            self.quit()

    def _model_state_changed(self) -> None:
        state = self.models.state
        if self._announce_model and state in ("ready", "error", "off"):
            self._announce_model = False
            if state == "ready":
                self.toast("소형 모델을 켰어요. 이름·주소 등을 더 잘 찾아요.")
            elif state == "error":
                self.toast(f"소형 모델을 켜지 못했어요. {self.models.message}", tone="error")
            elif self.settings.get("model_enabled", True):
                self.toast("모델 폴더를 먼저 지정하세요. 지금은 규칙·사전으로만 검사해요.", tone="warning")
            else:
                self.toast("소형 모델을 껐어요. 규칙·사전으로만 검사해요.")
        elif state == "error":
            print("model load error:", self.models.message)
        self._state_changed()
        for win in self.reviews:
            win.refresh()  # 모델이 준비되면 열린 창도 다시 검사

    # ================================================================== 클립보드 감시
    def _watch_clipboard(self) -> None:
        try:
            seq = platform_api.clipboard_sequence()
            if seq != self._last_seq:
                self._last_seq = seq
                if (self.settings["clipboard_watch"] and self.monitoring_active() and seq != self._own_seq
                        and not platform_api.own_window_in_foreground()):
                    text = platform_api.get_clipboard_text()
                    if text:
                        self._inspect_clipboard(text)
        except Exception as exc:  # 감시 루프는 죽지 않게
            print("clipboard watch error:", exc)
        finally:
            self.root.after(700, self._watch_clipboard)

    def _inspect_clipboard(self, text: str) -> None:
        if not self.settings["min_length"] <= len(text) <= MAX_CLIP_LEN:
            return
        # AI 답변을 복사한 경우: 서버 자리표시나 이 PC의 가짜 값이 들어 있으면 복원 창
        work = self.server_work
        if self.server_mode() and work and work.alive() and any(tok in text for tok in work.tokens):
            self.open_restore(text)
            return
        if self.session.entries and restore(text, self.session.entries)[1]:
            self.open_restore(text)
            return
        # 창을 띄울지는 이 PC의 규칙·사전으로 정한다. 복사한 모든 글(비밀번호 등)을 서버로 보내지 않기 위해서다.
        # 서버 처리 모드에서는 창이 열린 뒤에 서버가 전체 검사를 한다.
        model = self.detector() if len(text) <= CLIP_MODEL_MAX_CHARS and not self.server_mode() else None
        det = detect(text, self.base, self.ignore, model=model)
        if det.blockers or any(s.confidence >= AUTO_APPLY for s in det.spans) or det.coverage == "model_failed":
            self.open_review(text, "clipboard")

    def copy_to_clipboard(self, text: str) -> None:
        if not platform_api.set_clipboard_text(text):
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            self.root.update()
        self._own_seq = platform_api.clipboard_sequence()
        self._last_seq = self._own_seq

    # ================================================================== 창 열기
    def open_review(self, text: str = "", source: str = "manual", local: bool = False, selected=None) -> None:
        """서버 처리 모드면 서버 점검 창, 아니면(또는 local=True면) 이 PC 엔진 점검 창."""
        if not text and self.reviews:  # 빈 창 요청이면 기존 창을 앞으로
            self.reviews[-1]._bring_front()
            return
        cls = ServerReviewWindow if self.server_mode() and not local else ReviewWindow
        self.reviews.append(cls(self, text, source, selected=selected))

    def review_closed(self, win) -> None:
        if win in self.reviews:
            self.reviews.remove(win)

    def open_restore(self, text: str = "") -> None:
        cls = ServerRestoreWindow if self.server_mode() else RestoreWindow
        if self.restore_win and not isinstance(self.restore_win, cls):  # 처리 위치를 바꾼 뒤
            self.restore_win.destroy()
            self.restore_win = None
        if self.restore_win:
            if text:
                self.restore_win.src.delete("1.0", "end")
                self.restore_win.src.insert("1.0", text)
            self.restore_win.lift()
            self.restore_win.focus_force()
            return
        self.restore_win = cls(self, text)

    def restore_closed(self, win) -> None:
        if self.restore_win is win:
            self.restore_win = None

    def open_settings(self) -> None:
        if self.settings_win:
            self.settings_win.deiconify()
            self.settings_win.lift()
            self.settings_win.focus_force()
            return
        self.settings_win = SettingsWindow(self)

    def settings_closed(self) -> None:
        self.settings_win = None

    def open_files(self, path: str | None = None) -> None:
        if self.file_win:
            self.file_win.deiconify()
            self.file_win.lift()
            self.file_win.focus_force()
        else:
            self.file_win = FileWindow(self)
        if path:
            self.file_win.inspect(path)

    def files_closed(self) -> None:
        self.file_win = None

    # ================================================================== 알림
    def toast(self, message: str, ms: int = 3500, tone: str = "success", action=None) -> None:
        """알림: 화면 위 가운데. tone = success(완료) · warning(경고) · error(오류).
        action=(글자, 단축키, 함수)면 버튼과 남은 시간 막대를 둔다 (되돌리기는 10초)."""
        if self._toast_win is not None:
            self._toast_win.destroy()
        win = theme.toast(self.root, message, ms, tone=tone, action=action)
        self._toast_win = win
        win.after(ms, lambda: self._close_toast(win))

    def _close_toast(self, win) -> None:
        if self._toast_win is win:
            self._toast_win = None
        win.destroy()

    # ================================================================== 시작·종료
    def _first_run(self, background: bool) -> None:
        if not self.tray or not background:
            self.open_settings()
        if not self.settings["autostart_asked"]:
            self.settings["autostart_asked"] = True
            self.save_settings()
            ok = messagebox.askyesno(
                theme.APP_TITLE,
                f"Windows에 로그인할 때 {theme.APP_TITLE}를 자동으로 실행할까요?\n\n"
                "백그라운드에서 복사한 프롬프트를 점검합니다. 설정 창에서 언제든 끌 수 있어요.",
                parent=self.settings_win or self.root)
            if ok:
                try:
                    autostart.enable()
                except OSError as exc:
                    messagebox.showerror(theme.APP_TITLE, f"자동 시작 등록 실패: {exc}")
            if self.settings_win:
                self.settings_win.v_auto.set(autostart.is_enabled())
        if background and self.tray:
            self.tray.notify("브라우저의 Claude 입력창에서 감지된 단어를 클릭해 점검할 수 있습니다.")

    def quit(self) -> None:
        self.overlay.close()
        if self.browser:
            self.browser.stop()
        self.hotkeys.stop()
        if self.tray:
            self.tray.stop()
        try:
            self.log.export_jsonl()
        finally:
            self.log.close()
            self.root.destroy()
