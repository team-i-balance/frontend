"""GUI 흐름 자동 점검 (개발용). 임시 데이터 폴더에서 앱을 띄워 예시를 열고 사용자 행동을 흉내 낸 뒤 종료한다.

python tools/smoke_gui.py

- 파일 검사: openpyxl·defusedxml이 있으면 합성 xlsx로 보호본을 만들고 저장까지 해 본다.
- 소형 모델: onnx·onnxruntime·tokenizers가 있으면 tests/toy_model.py의 가짜 모델을 켜고 점검 창이 쓰는지 본다.
- 서버 처리: tests/fake_backend.py의 가짜 서버로 점검 → 되돌리기 → 변환문 전송 → 답변 복원을 해 본다.
  실제 backend로 해 보려면 KPGUARD_SMOKE_SERVER=http://127.0.0.1:8000 처럼 주소를 준다 (합성 데이터만 보낸다).
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="kpguard-smoke-")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tkinter import messagebox  # noqa: E402

messagebox.askyesno = lambda *a, **k: False  # 자동 시작 질문은 건너뛴다

from agent.files import processor  # noqa: E402
from agent.gui.app import App  # noqa: E402
from agent.gui.samples import SAMPLES  # noqa: E402
from tests import toy_model  # noqa: E402
from tests.fake_backend import FakeBackend  # noqa: E402

fake = None
if not os.environ.get("KPGUARD_SMOKE_SERVER"):
    fake = FakeBackend().start()
os.environ["KPGUARD_SERVER_URL"] = os.environ.get("KPGUARD_SMOKE_SERVER") or fake.url

app = App(background=False)
app.set_server("local")  # 앞부분은 이 PC 엔진 흐름, 서버 처리는 server_step에서
log = []
work = Path(os.environ["LOCALAPPDATA"])


def wait_for(cond, then, timeout=30.0, started=None):
    """조건이 맞을 때까지 메인 루프를 돌리며 기다린다."""
    started = started or time.time()
    if cond():
        then()
    elif time.time() - started > timeout:
        log.append(("시간 초과", then.__name__))
        app.quit()
    else:
        app.root.after(100, lambda: wait_for(cond, then, timeout, started))


def step():
    app._first_run(False)
    names = list(SAMPLES)
    app.open_review(SAMPLES[names[0]], "sample")
    w = app.reviews[-1]
    log.append(("상담", w.grade, w.result.text, w.cov_info.cget("text")))
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
    log.append(("모델 상태", app.settings_win.model_info.cget("text")))
    files_step()


def files_step():
    if not processor.available():
        log.append(("파일 검사", "건너뜀: openpyxl·defusedxml 없음"))
        model_step()
        return
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    book = Workbook()
    ws = book.active
    ws.append(["학생", "연락처"])
    ws.append(["홍길동 학생", "010-1234-5678"])
    ws.append(["홍길동", "010-2222-3333"])
    ws["A2"].comment = Comment("김민수 과장님 확인", "작성자")
    src = work / "상담명단.xlsx"
    book.save(src)
    app.post("files")

    def inspect():
        app.file_win.inspect(src)
        wait_for(lambda: app.file_win.report is not None, saved)

    def saved():
        fw = app.file_win
        log.append(("파일 검사", fw.report.status, fw.report.grade, fw.b_title.cget("text"), fw.details.cget("text")))
        out = work / fw.report.suggested_name
        log.append(("보호본 저장", fw.save_to(out), out.name, out.exists()))
        model_step()

    wait_for(lambda: app.file_win is not None, inspect)


def model_step():
    if not toy_model.deps_available():
        log.append(("소형 모델", "건너뜀: onnx·onnxruntime·tokenizers 없음"))
        server_step()
        return
    folder = toy_model.build(work / "toy-aegis")
    app.set_model(str(folder))

    def ready():
        app.open_review("어제 김민수랑 통화했어요. 번호는 010-1234-5678", "sample")
        w = app.reviews[-1]
        log.append(("모델로 점검", w.grade, w.result.text, w.cov_info.cget("text"),
                    [(it.span.type, it.span.source) for it in w.items]))
        app.settings_win.refresh()
        log.append(("모델 상태", app.settings_win.model_info.cget("text")))
        w.on_cancel()
        app.set_model(enabled=False)
        log.append(("모델 끔", app.models.state))
        server_step()

    wait_for(lambda: app.models.state in ("ready", "error"), ready)


def server_step():
    app.set_server("server")
    app.open_review("김민수 님 연락처는 010-1234-5678 이에요. 오로라 프로젝트 일정도 알려줘.", "sample")
    w = app.reviews[-1]

    def checked():
        log.append(("서버 점검", w.grade(), w.b_title.cget("text"), w.cov_info.cget("text"), w.outgoing,
                    [r[1:5] for r in w._rows()]))
        if fake:
            log.append(("서버로 보낸 글", fake.received[-1], "코드네임 포함" if "오로라" in fake.received[-1] else "코드네임 없음"))
        name = next((it for it in w.items if it.label == "이름" and it.exact), None)
        if name:
            w._set(w.reverted, name.token, "business_need")
            log.append(("서버 · 이름 되돌림", w.outgoing))
            w._set(w.reverted, name.token, None)
        w.on_send()
        wait_for(lambda: app.server_work is not None, restore)

    def restore():
        reply = "답변: " + " / ".join(sorted(app.server_work.tokens)) + " 그리고 [프로젝트A] 일정 확인"
        app._inspect_clipboard(reply)  # AI 답변 복사 → 서버 복원 창
        rw = app.restore_win
        log.append(("서버 복원 창", type(rw).__name__, rw.info.cget("text")))
        rw.run()
        wait_for(lambda: bool(rw.result), lambda: restored(rw))

    def restored(rw):
        log.append(("서버 복원", rw.result, rw.info.cget("text")))
        app.settings_win.refresh()
        log.append(("서버 설정", app.settings_win.server_info.cget("text")))
        rw._close()
        app.set_server("local")
        finish()

    wait_for(lambda: w.result is not None or w.error is not None, checked)


def finish():
    app.settings_win.refresh()
    log.append(("통계", app.settings_win.stats.cget("text")))
    if fake:
        fake.stop()
    app.root.after(800, app.quit)


app.root.after(600, step)
app.run()
for row in log:
    print("---", row[0], *row[1:], sep="\n")
