# KP Guard (프로토타입)

생성형 AI(Claude Code, Codex CLI, IDE 터미널, 명령 프롬프트, 웹 채팅)에 프롬프트를 보내기 전에
개인정보·기밀정보를 PC 안에서만 찾아 자연스러운 평문으로 바꿔 주는 도구. 외부 네트워크 연결 없음.

> 지금은 **일반 사용자용 GUI 흐름 확인용**이다. UI는 이후 교체한다. 작업 목록: `.kiro/specs/kp-guard/tasks.md`

## 실행

```powershell
pip install -r requirements.txt     # 선택: 트레이 아이콘 (없으면 설정 창 + 단축키로 동작)
python -m agent.main                 # 설정 창과 함께 시작
pythonw kp_guard.pyw --background    # 창 없이 트레이에 상주 (자동 시작이 쓰는 방식)
```

첫 실행 때 "Windows 로그인 시 자동 실행" 여부를 묻는다. 설정 창에서 언제든 켜고 끌 수 있다
(`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\KPGuard`, 관리자 권한 불필요).

## 데스크톱 앱 빌드

백그라운드에 상주하면서 프롬프트를 PC 안에서만 처리하고 기업 PC에 설치해 쓰는 도구라서, 웹 배포 없이 Windows 데스크톱 앱으로 배포한다.

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

결과물은 `dist\KPGuard\` 폴더이고 `KPGuard.exe`로 실행한다 (트레이 상주는 `KPGuard.exe --background`).
배포할 때는 폴더째 복사한다. 자동 시작은 exe의 현재 경로를 등록하므로, 폴더를 옮기면 설정 창에서 자동 시작을 껐다 다시 켠다.

## 흐름

1. 백그라운드 상주 (트레이 아이콘: 초록=감시 중, 주황=일시정지, 회색=꺼짐)
2. 트리거
   - CLI·IDE·메신저 등 어디서든 프롬프트를 **복사**하면 자동 점검 → 민감정보가 있으면 점검 창이 뜬다
   - `Ctrl+Alt+P`: 빈 점검 창을 열고 프롬프트를 직접 작성
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
├─ detect/                 rules(0단계) · lexicon(1단계 경량) · scoring(등급·조합 위험) · pipeline
├─ sanitize/               engine(전략·치환·문장 정리) · fakes · josa(조사 보정) · restore(답변 복원)
├─ events/log.py           SQLite 로그, classify() 큐 분류, JSONL 내보내기
├─ system/                 win32(클립보드·단축키·뮤텍스) · autostart(레지스트리 Run)
├─ gui/                    app(상주·감시) · review(점검 창) · restore_window · settings_window · tray
└─ assets/demo_base.json   시연용 가상 조직 베이스 (코드네임·고객사·내부 도메인)
tools/smoke_gui.py         GUI 흐름 자동 점검 (임시 폴더 사용)
build.ps1 · KPGuard.spec   데스크톱 앱(exe) 빌드
```

## 프로토타입 한계

- 탐지는 정규식 + 작은 사전. Kiwi·소형 NER 모델·NPU는 Phase 3~4
- CLI 입력을 직접 가로채지 않고 클립보드로 연결한다. Claude Code 훅·CLI 래퍼는 P0.11
- 매핑은 앱 메모리에만 있어 앱을 끄면 이전 답변은 복원할 수 없다 (의도된 동작)
- 모든 예시 값은 합성 데이터
- exe는 코드 서명이 없어 SmartScreen·백신 경고가 뜰 수 있고, 설치 프로그램은 아직 없다 (태스크 27)
