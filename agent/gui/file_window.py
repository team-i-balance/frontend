"""파일 검사 창 (§8, R3): xlsx를 AI에 올리기 전에 검사하고 민감정보를 가린 보호본을 만든다.

- 이 PC 처리: 이 PC의 엔진(규칙·사전·소형 모델)으로 검사하고 보호본을 만든다. 파일은 PC 밖으로 나가지 않는다.
- 서버 처리: 업로드 → 결과 → 보호본 받기 (files/remote.py). 서버에 닿지 않으면 '이 PC에서 검사'를 권한다.
검사는 작업 스레드에서 돌고(모델 추론·서버 요청이 길 수 있음), tkinter는 메인 스레드에서만 다룬다.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from ..detect.pipeline import COVERAGE_LABELS
from ..detect.span import TYPE_LABELS
from ..files.processor import FileReport, inspect_xlsx
from ..files.remote import STAGES, inspect_xlsx_server
from . import theme

INTRO = "숨긴 시트·행·열, 메모, 문서 속성까지 검사하고 보호본에는 보이는 값만 남겨요. 파일은 이 PC 밖으로 나가지 않아요."
INTRO_SERVER = ("숨긴 시트·행·열, 메모, 문서 속성은 이 PC에서 빼고 조직 베이스(코드네임·고객사·사내 도메인)를 가린 뒤, "
                "보이는 값만 팀 서버로 보내 검사해요. 원래 파일 이름은 보내지 않아요.")


def _counts(counter) -> str:
    return ", ".join(f"{TYPE_LABELS.get(t, t)} {n}" for t, n in counter.most_common())


def _removed(s) -> list[str]:
    return [f"{label} {n}개" for label, n in (("메모", s.removed_comments), ("숨긴 시트", s.removed_sheets),
                                              ("숨긴 행", s.removed_rows), ("숨긴 열", s.removed_columns)) if n]


class FileWindow(tk.Toplevel):
    """주요 버튼(앰버)은 지금 할 일 하나: 파일을 고르기 전에는 '파일 선택', 보호본이 준비되면 '보호본 저장'."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.report: FileReport | None = None
        self.source: Path | None = None
        self._q: queue.Queue = queue.Queue()
        self._busy = False
        self._job = 0

        theme.window(self, f"파일 검사 — {theme.APP_TITLE}", 760, 680, 620, 540)
        self.protocol("WM_DELETE_WINDOW", self._close)
        gap = theme.px(16)

        self.pick = theme.Card(self, title="엑셀 파일 검사", caption=self._intro(), fit=True)
        self.pick.pack(fill="x", padx=gap, pady=(gap, 0))
        self.pick.caption.configure(wraplength=theme.px(520))
        self.btn_pick = theme.Button(self.pick.actions, "파일 선택", self.choose, kind="primary")
        self.btn_pick.pack(side="right")
        self.file_label = theme.label(self.pick.body, "아직 고른 파일이 없어요", "label14", theme.TEXT_TERTIARY)
        self.file_label.pack(fill="x")

        self.banner = theme.Banner(self)
        self.banner.pack(fill="x", padx=gap, pady=(theme.px(12), 0))
        self.b_title = self.banner.title
        self.btn_retry = theme.Button(self.banner.actions, "다시 시도", self.retry, size="sm")
        self.btn_local = theme.Button(self.banner.actions, "이 PC에서 검사", self.inspect_here, size="sm")
        self.banner.set("neutral", "검사할 파일을 고르세요")

        bottom = tk.Frame(self, bg=theme.BG_CANVAS)
        bottom.pack(side="bottom", fill="x", padx=gap, pady=(theme.px(8), gap))
        self.btn_save = theme.Button(bottom, "보호본 저장", self.save)
        self.btn_save.configure(state="disabled")
        self.btn_save.pack(side="right")
        theme.Button(bottom, "닫기", self._close, kind="ghost").pack(side="right", padx=theme.px(8))

        result = theme.Card(self, title="검사 결과")
        result.pack(fill="both", expand=True, padx=gap, pady=(theme.px(12), 0))
        self.details = theme.label(result.body, "파일을 고르면 가린 값과 보호본에서 뺀 것이 여기에 나와요.", "body16r",
                                   theme.TEXT_TERTIARY)
        self.details.configure(anchor="nw")
        self.details.pack(fill="both", expand=True)
        result.bind("<Configure>", lambda e: self.details.configure(wraplength=max(theme.px(200), e.width - theme.px(64))),
                    add="+")
        self.after(100, self._poll)
        self.lift()
        self.focus_force()

    def _server(self) -> bool:
        return self.app.server_mode()

    def _intro(self) -> str:
        return INTRO_SERVER if self._server() else INTRO

    # ================================================================== 검사
    def choose(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="검사할 엑셀 파일",
                                          filetypes=[("Excel 통합 문서", "*.xlsx"), ("모든 파일", "*.*")])
        if path:
            self.inspect(path)

    def retry(self) -> None:
        if self.source:
            self.inspect(self.source)

    def inspect_here(self) -> None:
        """서버에 닿지 않거나 서버가 검사하지 못할 때: 같은 파일을 이 PC의 엔진으로 검사한다."""
        if self.source:
            self.inspect(self.source, local=True)

    def inspect(self, path, local: bool = False) -> None:
        if self._busy:
            return
        if Path(path).suffix.lower() != ".xlsx":
            self.app.toast(".xlsx 파일만 검사할 수 있어요", tone="warning")
            return
        self._busy = True
        self._job += 1
        job = self._job
        self.source = Path(path)
        self.report = None
        server = self._server() and not local
        self.pick.caption.configure(text=INTRO_SERVER if server else INTRO)
        self.file_label.configure(text=self.source.name)
        self.btn_save.configure(state="disabled")
        self.btn_save.set_kind("secondary")
        self.btn_pick.configure(state="disabled")
        self._paint("neutral", STAGES["local"] if server else "검사 중… 칸이 많으면 시간이 걸릴 수 있어요", "")
        app = self.app
        args = (self.source, app.base, app.session)
        ignore = frozenset(app.ignore)

        def stage(name: str) -> None:
            self._q.put((job, "stage", name))

        def work():
            try:
                if server:
                    report = inspect_xlsx_server(*args, app.server, ignore=ignore, progress=stage)
                else:
                    report = inspect_xlsx(*args, ignore=ignore, model=app.detector())
            except Exception:  # 예외 메시지에 파일 내용이 섞일 수 있어 보여 주지 않는다
                report = FileReport("FAILED", "PROCESSOR_ERROR", where="server" if server else "local")
            self._q.put((job, "done", report))

        threading.Thread(target=work, daemon=True, name="file-inspect").start()

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                job, kind, value = self._q.get_nowait()
                if job != self._job:
                    continue
                if kind == "stage":
                    self._paint("neutral", STAGES.get(value, "검사 중…"), "")
                else:
                    self._busy = False
                    self.btn_pick.configure(state="normal")
                    self.show(value)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ================================================================== 결과
    def _paint(self, tone: str, title: str, details: str, fallback: bool = False) -> None:
        for b in (self.btn_retry, self.btn_local):
            b.pack_forget()
        if fallback:
            self.btn_local.pack(side="right")
            self.btn_retry.pack(side="right", padx=(0, theme.px(8)))
        self.banner.set(tone, title)
        self.details.configure(text=details, fg=theme.TEXT_PRIMARY)

    def show(self, report: FileReport) -> None:
        self.report = report
        server = report.where == "server"
        lines = []
        if report.status == "READY":
            s = report.summary
            changed = sum(report.found.values())
            got = "받았어요" if server else "만들었어요"
            if report.grade == "S":
                title, tone = f"민감 · 값 {changed}개를 가린 보호본을 {got}", "warning"
            else:
                title, tone = f"공개 · 민감정보가 보이지 않아요. 값만 남긴 보호본을 {got}", "success"
            if report.found:
                lines.append(f"• 가린 값: {_counts(report.found)}")
            if server:
                if report.org_found:
                    lines.append(f"• 조직 베이스 {sum(report.org_found.values())}개는 서버로 보내기 전에 이 PC에서 가렸어요.")
                if _removed(s):
                    lines.append("• 이 PC에서 빼고 보낸 것: " + ", ".join(_removed(s)))
            else:
                if report.hidden_found:
                    lines.append(f"• 숨은 곳(메모·숨긴 시트·문서 속성 등)에서 찾은 민감정보: {_counts(report.hidden_found)} "
                                 "→ 보호본에서 빠져요")
                if _removed(s):
                    lines.append("• 보호본에서 뺀 것: " + ", ".join(_removed(s)))
                if report.suspects:
                    lines.append(f"• 확신이 낮아 그대로 둔 값: {_counts(report.suspects)} → 보호본을 열어 확인하세요")
            lines.append("• 시트 이름은 '시트1'부터 다시 붙이고, 서식·수식·문서 속성은 옮기지 않아요.")
            if server and report.server_tokens:
                lines.append("• 서버 자리표시([이름_1] 등)는 아직 AI 답변에서 원래 값으로 되돌릴 수 없어요 "
                             "(서버에 파일 복원 기능이 없어요).")
            if server and report.org_found:
                lines.append("• 조직 베이스 태그([프로젝트A] 등)는 '답변 복원'(Ctrl+Alt+R)에서 원래 값으로 바꿀 수 있어요.")
            elif not server and report.grade == "S":
                lines.append("• AI 답변에 나온 가짜 값은 '답변 복원'(Ctrl+Alt+R)에서 원래 값으로 바꿀 수 있어요.")
            if server:
                self.app.note_file_tokens(report.server_tokens)
            self.btn_save.configure(state="normal")
            self.btn_save.set_kind("primary")  # 지금 할 일: 저장
            self.btn_pick.set_kind("secondary")
            self.btn_pick.configure(text="다른 파일 선택")
        elif report.status == "BLOCKED":
            title, tone = "기밀 · 외부 AI 전송이 차단됐어요", "danger"
            lines.append(report.message)
            lines += [f"• {r}" for r in report.blocker_reasons[:5]]
            self._log("file_blocked", "C", report)
        else:
            title, tone = "보호본을 만들지 못했어요", "danger"
            lines.append(report.message or "알 수 없는 이유로 검사하지 못했어요.")
            lines.append("다시 시도하거나 이 PC에서 검사하세요." if report.fallback else "원본 파일을 그대로 올리지 마세요.")
        if server and self.app.server and self.app.server.insecure:
            lines.append("• 서버 연결이 암호화되지 않았어요(HTTP). 실제 개인정보로 쓰기 전에 HTTPS로 바꿔야 해요.")
        coverage = self._coverage(report)
        if coverage:
            lines.append(f"\n검사: {coverage}")
        self._paint(tone, title, "\n".join(lines), fallback=report.fallback)

    @staticmethod
    def _coverage(report: FileReport) -> str | None:
        if report.where != "server":
            return COVERAGE_LABELS.get(report.coverage, report.coverage)
        if report.status == "READY" or report.reason == "SERVER_CONFIDENTIAL":
            return "팀 서버(탐지 모델+규칙) · 조직 베이스·기밀 표기는 이 PC"
        if report.status == "BLOCKED":
            return "이 PC(조직 베이스·기밀 표기) · 파일을 서버로 보내지 않았어요"
        return None  # 검사가 끝나지 않았다

    def save(self) -> None:
        if not self.report or self.report.status != "READY":
            return
        folder = str(self.source.parent) if self.source else None
        path = filedialog.asksaveasfilename(parent=self, title="보호본 저장", defaultextension=".xlsx",
                                            initialdir=folder, initialfile=self.report.suggested_name,
                                            filetypes=[("Excel 통합 문서", "*.xlsx")])
        if path:
            self.save_to(path)

    def save_to(self, path) -> bool:
        path = Path(path)
        try:
            if path.suffix.lower() != ".xlsx":
                self.app.toast("보호본은 .xlsx 파일로 저장해 주세요", tone="warning")
                return False
            if self.source and (path.resolve() == self.source.resolve() or (path.exists() and path.samefile(self.source))):
                self.app.toast("원본 파일 위에는 저장할 수 없어요. 다른 이름을 고르세요", tone="warning")
                return False
            path.write_bytes(self.report.file_bytes)
        except OSError as exc:
            self.app.toast(f"저장하지 못했어요: {exc.strerror or exc}", tone="error")
            return False
        self._log("file_sanitized", self.report.grade, self.report)
        self.app.toast(f"보호본을 저장했어요: {path.name} · 이 파일을 AI에 올리세요")
        return True

    def _log(self, action: str, grade: str, report: FileReport) -> None:
        """원문 없이 유형·건수만 남긴다. 서버 파일 API는 기록을 남기지 않으므로 서버 처리 때도 이 PC에 남긴다.

        서버가 막은 파일은 무엇 때문인지 서버가 알려 주지 않아 유형 없이 차단 사실만 남긴다."""
        counts = report.found + report.hidden_found + report.blocker_types
        if not counts and action != "file_blocked":
            return
        service = "server:file:xlsx" if report.where == "server" else "file:xlsx"
        self.app.log.record(service=service, grade=grade, action=action, detect_types=list(counts),
                            counts=dict(counts), max_confidence=0.0)

    def _close(self) -> None:
        self._job += 1      # 늦게 온 결과는 버린다
        self.report = None  # 보호본 바이트를 메모리에 남기지 않는다
        self.app.files_closed()
        self.destroy()
