"""콘솔 창 없이 실행하는 런처 (pythonw kp_guard.pyw). 자동 시작 레지스트리가 이 파일을 가리킨다."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.main import main  # noqa: E402

main()
