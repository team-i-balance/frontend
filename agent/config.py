"""경로·설정·데모 조직 베이스 로딩. 설정 파일에는 원문을 저장하지 않는다."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "KPGuard"
ASSETS = Path(__file__).resolve().parent / "assets"
# backend#10 전 기본 주소 (평문 HTTP). 지금 서버는 uvicorn을 127.0.0.1에만 열고 Caddy가 443에서 HTTPS를 연다.
# 저장된 주소가 이 값 그대로면(사용자가 바꾸지 않았으면) 새 기본 주소로 옮긴다.
OLD_SERVER_URL = "http://13.193.207.69:8501"

DEFAULTS = {
    "native_overlay": True,
    "monitoring": True,        # 감시 켜기/끄기 (R2)
    "clipboard_watch": True,   # 복사한 프롬프트 자동 점검
    "autostart_asked": False,  # 첫 실행 시 자동 시작 동의를 물었는지
    "min_length": 6,           # 이보다 짧은 복사는 무시
    "model_dir": "",           # 2단계 소형 모델(AEGIS) 폴더. 비우면 기본 폴더에 모델이 있을 때만 켠다
    "model_enabled": True,     # 모델을 끄고 규칙·사전만 쓰려면 False
    "model_threads": 2,
    # 처리 위치 (2026-11-05 결정): server = 탐지·가명화·기록·복원을 팀 서버에서, local = 이 PC 엔진만
    "processing": "server",
    # 팀 backend (Caddy HTTPS). 도메인이 없어 자체 서명 인증서라 처음 연결할 때 지문을 확인해 고정한다 (backend#10).
    # 환경 변수로 덮어쓸 수 있다 (MDM 배포): KPGUARD_SERVER_URL, KPGUARD_API_TOKEN, KPGUARD_SERVER_PIN
    "server_url": "https://13.193.207.69",
    "server_timeout": 20,
    # 인증 (backend#10): dev = 서버가 바로 내주는 개발용 토큰, team = 관리자에게 받은 팀 토큰
    "auth_mode": "dev",
    "api_token": "",    # 팀 토큰. 이 PC 사용자 폴더의 설정 파일에 둔다 (DPAPI 보호는 태스크 24)
    "server_pins": {},  # "호스트:포트" → 신뢰한 서버 인증서 SHA-256 지문
}


def data_dir() -> Path:
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def server_url(settings: dict) -> str:
    return os.environ.get("KPGUARD_SERVER_URL") or settings.get("server_url", "")


def auth_mode(settings: dict) -> str:
    """team / dev. KPGUARD_API_TOKEN 환경 변수가 있으면 그 팀 토큰을 쓴다."""
    if os.environ.get("KPGUARD_API_TOKEN"):
        return "team"
    return "team" if settings.get("auth_mode") == "team" else "dev"


def api_token(settings: dict) -> str:
    """팀 토큰 ("" = 없음). 개발용 토큰은 저장하지 않고 앱이 실행 중일 때 서버에서 받는다."""
    env = os.environ.get("KPGUARD_API_TOKEN")
    if env:
        return env.strip()
    return (settings.get("api_token") or "").strip() if auth_mode(settings) == "team" else ""


def server_pin(settings: dict, url: str) -> str:
    """이 서버 주소에 고정한 인증서 지문 ("" = 없음, 시스템 인증서 저장소로 확인)."""
    from .server.client import server_key
    env = os.environ.get("KPGUARD_SERVER_PIN")
    if env:
        return env.strip()
    return (settings.get("server_pins") or {}).get(server_key(url), "") if url else ""


def default_model_dir() -> Path:
    """설치 프로그램이나 사용자가 모델을 넣어 둘 기본 위치: %LOCALAPPDATA%\\KPGuard\\models\\aegis"""
    return data_dir() / "models" / "aegis"


def model_dir(settings: dict) -> str:
    """켤 모델 폴더. 지정한 폴더가 우선이고, 없으면 기본 폴더에 모델 파일이 있을 때만 쓴다. 꺼져 있으면 ""."""
    if not settings.get("model_enabled", True):
        return ""
    if settings.get("model_dir"):
        return settings["model_dir"]
    from .detect.model import looks_like_model_dir
    folder = default_model_dir()
    return str(folder) if looks_like_model_dir(folder) else ""


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        settings.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError):
        pass
    if settings.get("server_url") == OLD_SERVER_URL:
        settings["server_url"] = DEFAULTS["server_url"]
    pins = settings.get("server_pins")
    # 기본값의 dict를 공유하지 않게 새로 만든다
    settings["server_pins"] = ({k: v for k, v in pins.items() if isinstance(k, str) and isinstance(v, str)}
                               if isinstance(pins, dict) else {})
    return settings


def save_settings(settings: dict) -> None:
    _settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_base() -> dict:
    """데모용 조직 베이스. 실제 조직 모드에서는 서명된 정책 번들(해시)로 대체한다 (§10)."""
    return json.loads((ASSETS / "demo_base.json").read_text(encoding="utf-8"))
