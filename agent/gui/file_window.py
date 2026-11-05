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

INTRO = ("엑셀(.xlsx) 파일을 AI에 올리기 전에 검사하고, 민감정보를 가린 보호본을 만들어요.\n"
         "숨긴 시트·행·열, 메모, 문서 속성까지 검사하고, 보호본에는 보이는 값만 남겨요. "
         "파일은 이 PC 밖으로 나가지 않아요.")


def _counts(counter) -> str:
    return ", ".join(f"{TYPE_LABELS.get(t, t)} {n}" for t, n in counter.most_common())


class FileWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.report: FileReport | None = None
        self.source: Path | None = None
        self._q: queue.Queue = queue.Queue()
        self._busy = False

        self.title("파일 검사 — KP Guard")
        self.geometry("640x560")
        self.minsize(560, 460)
        self.configure(bg="white", padx=18, pady=14)
        self.protocol("WM_DELETE_WINDOW", self._close)

        tk.Label(self, text="파일 검사", font=theme.TITLE, bg="white", anchor="w").pack(fill="x")
        intro = INTRO
        if app.server_mode():
            intro += "\n서버 처리 모드여도 파일 검사는 아직 이 PC에서 해요 (서버 파일 API가 보호본 파일을 돌려주지 않아요)."
        tk.Label(self, text=intro, font=theme.SMALL, bg="white", fg=theme.MUTED, anchor="w", justify="left",
                 wraplength=590).pack(fill="x", pady=(2, 10))
        row = tk.Frame(self, bg="white")
        row.pack(fill="x")
        self.btn_pick = theme.primary_button(row, "xlsx 파일 선택…", self.choose)
        self.btn_pick.pack(side="left")
        self.file_label = tk.Label(row, text="", font=theme.SMALL, bg="white", fg=theme.MUTED, anchor="w")
        self.file_label.pack(side="left", padx=10, fill="x", expand=True)

        self.banner = tk.Frame(self, padx=14, pady=10, bg=theme.BG)
        self.banner.pack(fill="x", pady=(12, 6))
        self.b_title = tk.Label(self.banner, text="검사할 파일을 고르세요", font=theme.BOLD, bg=theme.BG,
                                anchor="w", justify="left", wraplength=580)
        self.b_title.pack(fill="x")
        self.details = tk.Label(self, text="", font=theme.FONT, bg="white", anchor="nw", justify="left",
                                wraplength=590)
        self.details.pack(fill="both", expand=True)

        bottom = tk.Frame(self, bg="white")
        bottom.pack(fill="x", pady=(10, 0))
        self.btn_save = theme.primary_button(bottom, "보호본 저장…", self.save)
        self.btn_save.configure(state="disabled")
        self.btn_save.pack(side="right")
        theme.plain_button(bottom, "닫기", self._close).pack(side="right", padx=8)
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
        self.btn_pick.configure(state="disabled")
        self._paint(theme.BG, "#111827", "검사 중… 칸이 많으면 시간이 걸릴 수 있어요", "")
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
    def _paint(self, bg: str, fg: str, title: str, details: str) -> None:
        self.banner.configure(bg=bg)
        self.b_title.configure(bg=bg, fg=fg, text=title)
        self.details.configure(text=details)

    def show(self, report: FileReport) -> None:
        self.report = report
        lines = []
        if report.status == "READY":
            s = report.summary
            changed = sum(report.found.values())
            if report.grade == "S":
                title = f"민감 · 값 {changed}개를 가린 보호본을 만들었어요"
                bg, fg = theme.GRADE["S"]
            else:
                title = "공개 · 민감정보가 보이지 않아요. 값만 남긴 보호본을 만들었어요"
                bg, fg = theme.GRADE["O"]
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
        elif report.status == "BLOCKED":
            title = "기밀 · 외부 AI 전송이 차단됐어요"
            bg, fg = theme.GRADE["C"]
            lines.append(report.message)
            lines += [f"• {r}" for r in report.blocker_reasons[:5]]
            self._log("file_blocked", "C", report)
        else:
            title = "보호본을 만들지 못했어요"
            bg, fg = "#f3f4f6", "#374151"
            lines.append(report.message or "알 수 없는 이유로 검사하지 못했어요.")
            lines.append("원본 파일을 그대로 올리지 마세요.")
        lines.append(f"\n검사: {COVERAGE_LABELS.get(report.coverage, report.coverage)}")
        self._paint(bg, fg, title, "\n".join(lines))

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
            self.app.toast("원본 파일 위에는 저장할 수 없어요. 다른 이름을 고르세요.")
            return False
        try:
            path.write_bytes(self.report.file_bytes)
        except OSError as exc:
            self.app.toast(f"저장하지 못했어요: {exc.strerror or exc}")
            return False
        self._log("file_sanitized", self.report.grade, self.report)
        self.app.toast(f"보호본을 저장했어요: {path.name}  이 파일을 AI에 올리세요.")
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
