"""외부 연결 없음: 앱 모듈을 불러오기만 해도 onnxruntime 원격 수집이 꺼져야 한다 (backend#13과 같은 검사).

값은 onnxruntime을 불러오기 전에 있어야 효과가 있다. 이미 여러 모듈을 불러온 테스트 프로세스로는
아무것도 증명하지 못하므로, 매번 새 인터프리터에서 확인한다 (backend tests/test_no_telemetry.py와 같은 방식).
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAR = "ORT_DISABLE_TELEMETRY"
REPORT = ("import json, os, sys; print(json.dumps({'env': os.environ.get(%r), "
          "'ort': 'onnxruntime' in sys.modules}))" % VAR)


def run(code: str, **env_overrides: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != VAR}
    env.update(env_overrides)
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout.strip().splitlines()[-1])


class NoTelemetryTests(unittest.TestCase):
    def test_model_module_switches_telemetry_off_before_onnxruntime(self):
        # 모델 모듈을 불러와도 onnxruntime은 아직 불러오지 않는다 (모델을 켤 때 불러온다) → 그 전에 값이 있다
        self.assertEqual(run("import agent.detect.model\n" + REPORT), {"env": "1", "ort": False})

    def test_entry_point_sets_it_first(self):
        self.assertEqual(run("import agent.main\n" + REPORT), {"env": "1", "ort": False})

    def test_explicit_setting_is_kept(self):
        self.assertEqual(run("import agent.detect.model\n" + REPORT, **{VAR: "0"})["env"], "0")


if __name__ == "__main__":
    unittest.main()
