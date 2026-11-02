"""schedule.sh: the one-command recommended setup, checked with --dry-run (nothing is installed)."""

import os
import shutil
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


def test_doctor_runs_inside_the_scheduler_dry_run(tmp_path):
    env = {**os.environ, "SCHEDULE_OS": "Darwin", "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(ROOT / "schedule.sh"), "doctor", "--dry-run"], capture_output=True, text=True,
                         env=env, timeout=60)
    assert out.returncode == 0 and "doctor.py --verbose" in out.stdout and "doctor-scheduled.log" in out.stdout


def test_launchd_jobs_keep_their_folder_and_log_off_the_project_drive(tmp_path):
    """launchd can't start a job whose folder or log is on an external drive (exit 78, no message anywhere)."""
    env = {**os.environ, "SCHEDULE_OS": "Darwin", "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(ROOT / "schedule.sh"), "recommended", "--dry-run"], capture_output=True,
                         text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert f"<key>WorkingDirectory</key><string>{tmp_path}</string>" in out.stdout
    assert f"<string>{tmp_path}/Library/Logs/crawl4ai/scheduler.log</string>" in out.stdout
    assert f"<string>{tmp_path}/Library/Logs/crawl4ai/watch.log</string>" in out.stdout
    assert f"<string>{ROOT}/logs/" not in out.stdout and f"<string>{ROOT}</string>" not in out.stdout
    # macOS judges a script named directly in the job, not /bin/bash: start it through bash -c instead
    assert f"<string>{ROOT}/run_all.sh</string>" not in out.stdout
    assert f"<string>{ROOT}/watch.sh</string>" not in out.stdout
    assert f"<string>-c</string><string>cd '{ROOT}' || exit 1; exec /bin/bash ./watch.sh --foreground</string>" \
        in out.stdout


def test_status_explains_old_schedules_and_the_macos_block(tmp_path):
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.crawl4ai.runall.frequent.plist").write_text(
        f"<key>StartInterval</key><integer>900</integer>\n"
        f"<key>StandardOutPath</key><string>{ROOT}/logs/scheduler.log</string>\n")
    job_logs = tmp_path / "Library" / "Logs" / "crawl4ai"
    job_logs.mkdir(parents=True)
    (job_logs / "watch.log").write_text(f"/bin/bash: {ROOT}/watch.sh: Operation not permitted\n")
    env = {**os.environ, "SCHEDULE_OS": "Darwin", "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(ROOT / "schedule.sh"), "status"], capture_output=True, text=True, env=env,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert "set up by an older version" in out.stdout and "./schedule.sh recommended" in out.stdout
    assert "Full Disk Access" in out.stdout and "/bin/bash" in out.stdout


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
@pytest.mark.parametrize("script", ["run_all.sh", "watch.sh"])
def test_scripts_say_clearly_when_the_project_folder_is_blocked(script, tmp_path):
    shutil.copy(ROOT / script, tmp_path / script)
    logs = tmp_path / "logs"
    logs.mkdir()
    logs.chmod(0o500)                       # like macOS blocking the drive: logs/ can't be written
    try:
        out = subprocess.run(["bash", str(tmp_path / script), "--status"], capture_output=True, text=True,
                             timeout=60)
    finally:
        logs.chmod(0o700)
    assert out.returncode == 1
    assert "Operation not permitted" in out.stderr and "Full Disk Access" in out.stderr


def test_status_forgets_the_macos_block_once_a_run_gets_in(tmp_path):
    """A run that works writes nothing else to launchd's log, so only its last line counts."""
    job_logs = tmp_path / "Library" / "Logs" / "crawl4ai"
    job_logs.mkdir(parents=True)
    (job_logs / "scheduler.log").write_text(f"/bin/bash: {ROOT}/run_all.sh: Operation not permitted\n"
                                            f"2026-11-03 05:15:00 run started, log: {ROOT}/logs/run-x.log\n")
    env = {**os.environ, "SCHEDULE_OS": "Darwin", "HOME": str(tmp_path)}
    out = subprocess.run(["bash", str(ROOT / "schedule.sh"), "status"], capture_output=True, text=True, env=env,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    assert "Problem" not in out.stdout
