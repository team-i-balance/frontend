"""경로·설정·데모 조직 베이스 로딩. 설정 파일에는 원문을 저장하지 않는다."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "KPGuard"
ASSETS = Path(__file__).resolve().parent / "assets"

DEFAULTS = {
    "monitoring": True,        # 감시 켜기/끄기 (R2)
    "clipboard_watch": True,   # 복사한 프롬프트 자동 점검
    "autostart_asked": False,  # 첫 실행 시 자동 시작 동의를 물었는지
    "min_length": 6,           # 이보다 짧은 복사는 무시
}


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    settings = dict(DEFAULTS)
    try:
        settings.update(json.loads(_settings_path().read_text(encoding="utf-8")))
    except (FileNotFoundError, ValueError):
        pass
    return settings


def save_settings(settings: dict) -> None:
    _settings_path().write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def load_base() -> dict:
    """데모용 조직 베이스. 실제 조직 모드에서는 서명된 정책 번들(해시)로 대체한다 (§10)."""
    return json.loads((ASSETS / "demo_base.json").read_text(encoding="utf-8"))
