import subprocess
import sys


def test_xgboost_gpu_smoke_does_not_fall_back_for_prediction():
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.verify_gpu"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Falling back to prediction" not in completed.stderr
