import subprocess
import sys


def test_simple_test_reports_requests_runtime():
    result = subprocess.run(
        [sys.executable, "simple_test.py"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "requests import: ok" in result.stdout
    assert f"interpreter: {sys.executable}" in result.stdout
    assert "requests version:" in result.stdout
