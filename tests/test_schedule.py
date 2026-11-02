"""schedule.sh: the one-command recommended setup, checked with --dry-run (nothing is installed)."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("os_name", ["Darwin", "Linux"])
def test_recommended_sets_up_listener_frequent_and_hourly(os_name, tmp_path):
    env = {**os.environ, "SCHEDULE_OS": os_name, "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(ROOT / "schedule.sh"), "recommended", "--dry-run"], capture_output=True,
                         text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    text = out.stdout
    for step in ("watch on", "every 15m --profile frequent", "every 1h --profile hourly"):
        assert f"./schedule.sh {step}" in text
    if os_name == "Darwin":
        assert "com.crawl4ai.techwatch" in text and "<integer>900</integer>" in text and "<integer>3600</integer>" in text
    else:
        assert "--profile frequent" in text and "--profile hourly" in text and "watch.sh" in text
    assert not (tmp_path / "Library").exists()        # dry run: nothing written
