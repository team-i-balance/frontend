"""GUI 흐름 자동 점검 (개발용). 임시 데이터 폴더에서 앱을 띄워 예시를 열고 사용자 행동을 흉내 낸 뒤 종료한다.

python tools/smoke_gui.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="kpguard-smoke-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tkinter import messagebox  # noqa: E402

messagebox.askyesno = lambda *a, **k: False  # 자동 시작 질문은 건너뛴다

from agent.gui.app import App  # noqa: E402
from agent.gui.samples import SAMPLES  # noqa: E402

app = App(background=False)
log = []


def step():
    app._first_run(False)
    names = list(SAMPLES)
    w = app.reviews and app.reviews[-1]
    app.open_review(SAMPLES[names[0]], "sample")
    w = app.reviews[-1]
    log.append(("상담", w.grade, w.result.text))
    # 이름 되돌리기(업무상 필요) → 변환문에서 원래 이름이 보여야 함
    name_item = next(it for it in w.items if it.span.type == "name")
    w._decide(name_item, applied=False, reason="business_need")
    log.append(("이름 되돌림", w.grade, w.result.text))
    # 주소: 삭제 → 일반화
    addr = next(it for it in w.items if it.span.type == "address")
    w._decide(addr, applied=True, strategy="generalize")
    log.append(("주소 일반화", w.grade, w.result.text))
    w._report_missed("other", "성적 문제")
    log.append(("놓친 정보 신고", w.grade, w.result.text))
    w.on_send()

    app.open_review(SAMPLES[names[3]], "sample")
    w = app.reviews[-1]
    log.append(("소스코드", w.grade, w.btn_send.cget("state")))
    blk = next(it for it in w.items if it.is_blocker)
    w._decide(blk, applied=True, strategy="delete")
    log.append(("코드 빼고", w.grade, w.result.text))
    w.on_cancel()

    app.open_restore("빈가람 학생의 학부모님께. 다온고 2학년 ... [가림] 관련 상담 결과를 안내드립니다.")
    log.append(("복원", app.restore_win.result))
    app.open_settings()
    app.settings_win.refresh()
    log.append(("통계", app.settings_win.stats.cget("text")))
    app.root.after(800, app.quit)


app.root.after(600, step)
app.run()
for row in log:
    print("---", row[0], *row[1:], sep="\n")
