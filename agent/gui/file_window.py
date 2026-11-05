"""파일 검사 창 (§8, R3): xlsx를 AI에 올리기 전에 검사하고 민감정보를 가린 보호본을 만든다.

검사는 작업 스레드에서 돌고(모델 추론이 길 수 있음), tkinter는 메인 스레드에서만 다룬다.
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
from . import theme

INTRO = "숨긴 시트·행·열, 메모, 문서 속성까지 검사하고 보호본에는 보이는 값만 남겨요. 파일은 이 PC 밖으로 나가지 않아요."


def _counts(counter) -> str:
    return ", ".join(f"{TYPE_LABELS.get(t, t)} {n}" for t, n in counter.most_common())


class FileWindow(tk.Toplevel):
    """주요 버튼(앰버)은 지금 할 일 하나: 파일을 고르기 전에는 '파일 선택', 보호본이 준비되면 '보호본 저장'."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.report: FileReport | None = None
        self.source: Path | None = None
        self._q: queue.Queue = queue.Queue()
        self._busy = False

        theme.window(self, "파일 검사 — KP Guard", 760, 660, 620, 520)
        self.protocol("WM_DELETE_WINDOW", self._close)
        gap = theme.px(16)

        intro = INTRO
        if app.server_mode():
            intro += "\n서버 처리 모드여도 파일 검사는 아직 이 PC에서 해요 (서버 파일 API가 보호본 파일을 돌려주지 않아요)."
        pick = theme.Card(self, title="엑셀 파일 검사", caption=intro, fit=True)
        pick.pack(fill="x", padx=gap, pady=(gap, 0))
        pick.caption.configure(wraplength=theme.px(520))
        self.btn_pick = theme.Button(pick.actions, "파일 선택", self.choose, kind="primary")
        self.btn_pick.pack(side="right")
        self.file_label = theme.label(pick.body, "아직 고른 파일이 없어요", "label14", theme.TEXT_TERTIARY)
        self.file_label.pack(fill="x")

        self.banner = theme.Banner(self)
        self.banner.pack(fill="x", padx=gap, pady=(theme.px(12), 0))
        self.banner.set("neutral", "검사할 파일을 고르세요")
        self.b_title = self.banner.title

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
        self.lift()
        self.focus_force()

    # ================================================================== 검사
    def choose(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="검사할 엑셀 파일",
                                          filetypes=[("Excel 통합 문서", "*.xlsx"), ("모든 파일", "*.*")])
        if path:
            self.inspect(path)

    def inspect(self, path) -> None:
        if self._busy:
            return
        self._busy = True
        self.source = Path(path)
        self.report = None
        self.file_label.configure(text=self.source.name)
        self.btn_save.configure(state="disabled")
        self.btn_save.set_kind("secondary")
        self.btn_pick.configure(state="disabled")
        self._paint("neutral", "검사 중… 칸이 많으면 시간이 걸릴 수 있어요", "")
        app = self.app
        args = (self.source, app.base, app.session)
        kwargs = {"ignore": frozenset(app.ignore), "model": app.detector()}

        def work():
            try:
                report = inspect_xlsx(*args, **kwargs)
            except Exception:  # 예외 메시지에 파일 내용이 섞일 수 있어 보여 주지 않는다
                report = FileReport("FAILED", "PROCESSOR_ERROR")
            self._q.put(report)

        threading.Thread(target=work, daemon=True, name="file-inspect").start()
        self.after(100, self._poll)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            report = self._q.get_nowait()
        except queue.Empty:
            self.after(100, self._poll)
            return
        self._busy = False
        self.btn_pick.configure(state="normal")
        self.show(report)

    # ================================================================== 결과
    def _paint(self, tone: str, title: str, details: str) -> None:
        self.banner.set(tone, title)
        self.details.configure(text=details, fg=theme.TEXT_PRIMARY)

    def show(self, report: FileReport) -> None:
        self.report = report
        lines = []
        if report.status == "READY":
            s = report.summary
            changed = sum(report.found.values())
            if report.grade == "S":
                title, tone = f"민감 · 값 {changed}개를 가린 보호본을 만들었어요", "warning"
            else:
                title, tone = "공개 · 민감정보가 보이지 않아요. 값만 남긴 보호본을 만들었어요", "success"
            if report.found:
                lines.append(f"• 가린 값: {_counts(report.found)}")
            if report.hidden_found:
                lines.append(f"• 숨은 곳(메모·숨긴 시트·문서 속성 등)에서 찾은 민감정보: {_counts(report.hidden_found)} "
                             "→ 보호본에서 빠져요")
            removed = [f"{label} {n}개" for label, n in (("메모", s.removed_comments), ("숨긴 시트", s.removed_sheets),
                                                          ("숨긴 행", s.removed_rows), ("숨긴 열", s.removed_columns)) if n]
            if removed:
                lines.append("• 보호본에서 뺀 것: " + ", ".join(removed))
            if report.suspects:
                lines.append(f"• 확신이 낮아 그대로 둔 값: {_counts(report.suspects)} → 보호본을 열어 확인하세요")
            lines.append("• 시트 이름은 '시트1'부터 다시 붙이고, 서식·수식·문서 속성은 옮기지 않아요.")
            if report.grade == "S":
                lines.append("• AI 답변에 나온 가짜 값은 '답변 복원'(Ctrl+Alt+R)에서 원래 값으로 바꿀 수 있어요.")
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
            lines.append("원본 파일을 그대로 올리지 마세요.")
        lines.append(f"\n검사: {COVERAGE_LABELS.get(report.coverage, report.coverage)}")
        self._paint(tone, title, "\n".join(lines))

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
        if self.source and path.resolve() == self.source.resolve():
            self.app.toast("원본 파일 위에는 저장할 수 없어요. 다른 이름을 고르세요")
            return False
        try:
            path.write_bytes(self.report.file_bytes)
        except OSError as exc:
            self.app.toast(f"저장하지 못했어요: {exc.strerror or exc}")
            return False
        self._log("file_sanitized", self.report.grade, self.report)
        self.app.toast(f"보호본을 저장했어요: {path.name} · 이 파일을 AI에 올리세요")
        return True

    def _log(self, action: str, grade: str, report: FileReport) -> None:
        """원문 없이 유형·건수만 남긴다."""
        counts = report.found + report.hidden_found + report.blocker_types
        if not counts:
            return
        self.app.log.record(service="file:xlsx", grade=grade, action=action, detect_types=list(counts),
                            counts=dict(counts), max_confidence=0.0)

    def _close(self) -> None:
        self.report = None  # 보호본 바이트를 메모리에 남기지 않는다
        self.app.files_closed()
        self.destroy()
