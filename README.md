# KP Guard (프로토타입)

생성형 AI(Claude Code, Codex CLI, IDE 터미널, 명령 프롬프트, 웹 채팅)에 프롬프트를 보내기 전에
개인정보·기밀정보를 PC 안에서만 찾아 자연스러운 평문으로 바꿔 주는 도구. 외부 네트워크 연결 없음.

> 지금은 **일반 사용자용 GUI 흐름 확인용**이다. UI는 이후 교체한다. 작업 목록: `.kiro/specs/kp-guard/tasks.md`

## 실행

```powershell
pip install -r requirements.txt     # 선택: 트레이 아이콘, 파일 검사, 소형 모델 실행 모듈 (없으면 그 기능만 꺼짐)
python -m agent.main                 # 설정 창과 함께 시작
pythonw kp_guard.pyw --background    # 창 없이 트레이에 상주 (자동 시작이 쓰는 방식)
```

첫 실행 때 "Windows 로그인 시 자동 실행" 여부를 묻는다. 설정 창에서 언제든 켜고 끌 수 있다
(`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\KPGuard`, 관리자 권한 불필요).

## 탐지 단계

탐지는 모두 이 PC 안에서 한다. 원문을 서버로 보내지 않는다.

| 단계 | 내용 | 켜지는 조건 |
|---|---|---|
| 0 규칙 | 주민·카드·계좌·전화·이메일·IP·API 키 (검증식·문맥 점수) | 항상 |
| 1 사전·베이스 | 이름(성씨+호칭)·나이·주소·학교, 조직 베이스(코드네임·고객사·내부 도메인), 기밀 표기·사내 소스코드 | 항상 |
| 2 소형 모델 | AEGIS ONNX 개인정보 모델 (팀 backend `local_ai`): 이름·주소·생년월일·신분증 번호·계정 등 | 모델 폴더를 지정했을 때 |
| 2 SLM | 미세조정 GLiNER (팀 `slm` 레포): 회사 기밀 구간·코드네임 | 모델 파일을 받은 뒤 ONNX로 변환해 연결 예정 |

**소형 모델(AEGIS) 켜기.** 모델 파일은 레포와 exe에 들어 있지 않다. 아래 파일이 든 폴더를
설정 창의 "모델 폴더 선택…"으로 지정하거나 `%LOCALAPPDATA%\KPGuard\models\aegis`에 넣으면 다음 실행부터 켜진다.

```
model.onnx (또는 model_quantized.onnx, onnx/model_quantized.onnx)
tokenizer.json
config.json   (id2label)
```

- 모델은 백그라운드에서 불러오고, 준비되기 전에는 규칙·사전만으로 검사한다. 점검 창 오른쪽 위에 검사 범위가 나온다.
- 모델 추론이 실패하면 그 글은 "규칙·사전만"으로 표시하고 경고한다. 파일 검사는 보호본을 만들지 않는다.
- 확률이 0.6 미만인 모델 결과는 바로 바꾸지 않고 밑줄(확인 필요)로 보여 준다.
- onnxruntime은 CPU로 실행하고 텔레메트리를 끈다. 실행 중에 모델을 내려받지 않는다.

## 파일 검사 (엑셀 보호본)

`Ctrl+Alt+F`, 트레이 메뉴, 설정 창에서 연다. `.xlsx`를 고르면 팀 backend `file_sanitizer`로 표를 읽고,
칸마다 점검 창과 같은 엔진으로 가린 **보호본**을 만든다. 원본 파일은 바꾸지 않는다.

- 숨긴 시트·행·열, 메모, 문서 속성, 파일 이름, 숫자 서식 문구까지 검사한다. 보호본에는 보이는 값만 남는다
- "홍길동 학생" 칸에서 이름으로 확인한 값은 문맥이 없는 다른 칸의 "홍길동"도 함께 가린다
- 프롬프트와 같은 매핑을 써서 AI 답변의 가짜 값을 답변 복원 창에서 되돌릴 수 있다
- 기밀 표기가 있으면 파일 전체를 차단한다. 보호본을 다시 읽어 가린 값이 남았으면 보호본을 내놓지 않는다
- 수식·매크로·차트·병합 셀·필터 등은 아직 지원하지 않는다 (값만 남긴 표로 저장해서 다시 검사)
- 로그에는 유형·건수만 남는다

## 데스크톱 앱 빌드

백그라운드에 상주하면서 프롬프트를 PC 안에서만 처리하고 기업 PC에 설치해 쓰는 도구라서, 웹 배포 없이 Windows 데스크톱 앱으로 배포한다.

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

결과물은 `dist\KPGuard\` 폴더이고 `KPGuard.exe`로 실행한다 (트레이 상주는 `KPGuard.exe --background`).
배포할 때는 폴더째 복사한다. 자동 시작은 exe의 현재 경로를 등록하므로, 폴더를 옮기면 설정 창에서 자동 시작을 껐다 다시 켠다.
exe에는 onnxruntime 등 실행 모듈만 들어가고 모델 파일은 들어가지 않는다 (위의 "소형 모델 켜기" 참고).

## 테스트

```powershell
pip install -r requirements-dev.txt
python -m unittest discover -s tests -t . -v   # 탐지·모델 연동·파일 검사 (모델은 tests/toy_model.py의 가짜 모델)
python tools/smoke_gui.py                      # GUI 흐름 자동 점검
```

## 흐름

1. 백그라운드 상주 (트레이 아이콘: 초록=감시 중, 주황=일시정지, 회색=꺼짐)
2. 트리거
   - CLI·IDE·메신저 등 어디서든 프롬프트를 **복사**하면 자동 점검 → 민감정보가 있으면 점검 창이 뜬다
   - `Ctrl+Alt+P`: 빈 점검 창을 열고 프롬프트를 직접 작성
   - `Ctrl+Alt+F`: 엑셀 파일 검사 (보호본 만들기)
3. 점검 창: 왼쪽 원문(수정 가능) / 오른쪽 변환문, 유형별 하이라이트
4. **하이라이트(또는 표의 행) 클릭 → 사용자 행동**
   - 되돌리기 + 사유 (오탐 / 업무상 필요 / 승인받음 / 선택 안 함)
   - 다시 가리기, 변환 방식 바꾸기 (대체·일반화·삭제·태그)
   - 이 값은 앞으로 탐지하지 않기 (오탐 등록)
   - 기밀(빨간색): 이 부분 빼고 보내기 / 차단 해제(사유)
   - 밑줄 항목 = 확인 필요(자동 변환 안 함), 클릭해서 가리기
5. 놓친 정보: 원문에서 드래그 → 우클릭 → "이것도 가려줘"
6. **[변환문 복사]** → CLI에 붙여넣기 / [취소] / [원문 그대로 사용](사유 선택)
7. AI 답변을 복사하면 가짜 값이 있는지 확인해 **답변 복원 창**이 자동으로 뜬다 (`Ctrl+Alt+R`로 직접 열기)
8. 행동은 큐로 분류돼 로그에 남는다 (원문 없음): `%LOCALAPPDATA%\KPGuard\events.db`, `logs\YYYY-MM-DD.jsonl`

트레이 메뉴의 "예시로 점검해보기"에서 상담 안내문·코드네임·API 키·사내 소스코드·공개 질문 예시를 바로 열 수 있다.

## 구조

```
agent/
├─ main.py                 진입점 (단일 인스턴스, DPI)
├─ config.py               설정·경로·데모 조직 베이스
├─ detect/                 rules(0단계) · lexicon(1단계 경량) · model(2단계 AEGIS ONNX) · scoring(등급·조합 위험) · pipeline
├─ sanitize/               engine(전략·치환·문장 정리) · fakes · josa(조사 보정) · restore(답변 복원)
├─ files/                  processor(엔진 연결·보호본 재검사) · xlsx/(backend file_sanitizer 사본)
├─ events/log.py           SQLite 로그, classify() 큐 분류, JSONL 내보내기
├─ system/                 win32(클립보드·단축키·뮤텍스) · autostart(레지스트리 Run)
├─ gui/                    app(상주·감시) · review(점검 창) · file_window(파일 검사) · restore_window · settings_window · tray
└─ assets/demo_base.json   시연용 가상 조직 베이스 (코드네임·고객사·내부 도메인)
tests/                     unittest (toy_model.py: 테스트용 가짜 AEGIS 모델)
tools/smoke_gui.py         GUI 흐름 자동 점검 (임시 폴더 사용)
build.ps1 · KPGuard.spec   데스크톱 앱(exe) 빌드
```

## 프로토타입 한계

- 기본 탐지는 정규식 + 작은 사전. 소형 모델(AEGIS)은 모델 파일을 지정해야 켜지고, 실제 모델로는 아직 측정하지 않았다
  (CPU 실행만, INT8 변환·NPU는 Phase 4). SLM(기밀 구간)은 모델 파일을 받은 뒤 연결한다
- 파일 검사는 값만 있는 `.xlsx`만 지원한다. docx·pptx·hwp·pdf는 Phase 2 남은 작업
- CLI 입력을 직접 가로채지 않고 클립보드로 연결한다. Claude Code 훅·CLI 래퍼는 P0.11
- 매핑은 앱 메모리에만 있어 앱을 끄면 이전 답변은 복원할 수 없다 (의도된 동작)
- 모든 예시 값은 합성 데이터
- exe는 코드 서명이 없어 SmartScreen·백신 경고가 뜰 수 있고, 설치 프로그램은 아직 없다 (태스크 27)
