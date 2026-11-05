#!/bin/sh
set -eu
if [ "$(uname -s)" != Darwin ]; then
  echo 'macOS에서 실행해야 합니다.' >&2
  exit 1
fi
cd "$(dirname "$0")"
python3 -m PyInstaller --noconfirm KPGuard-macos.spec
printf '%s\n' '빌드 완료: dist/KPGuard.app'
