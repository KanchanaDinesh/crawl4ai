"""dashboard.sh: start, stop and status of the web app, against a stand-in server (a fake `uv`), so no real server or database is touched."""

import os
import socket
import stat
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "dashboard.sh"

FAKE_SERVER = f"""import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
port = int(sys.argv[-1].rsplit(":", 1)[-1])          # "127.0.0.1:8060", or just "8060" as typed by hand
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302); self.send_header("Location", "/"); self.end_headers()
    def log_message(self, *a):
        pass
print("Starting development server at", sys.argv[-1], flush=True)
HTTPServer(("127.0.0.1", port), H).serve_forever()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def world(tmp_path):
    """A bin folder with a fake uv, pg_isready and open, ahead of the real ones on the PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "server.py").write_text(FAKE_SERVER)
    executable(bin_dir / "uv", f"""#!/bin/bash
case "$*" in
  *"migrate --check"*) exit ${{FAKE_MIGRATE_RC:-0}} ;;
  *runserver*) if [ -n "${{FAKE_CRASH:-}}" ]; then echo "Error: something broke" >&2; exit 1; fi
               exec "{sys.executable}" "{tmp_path / 'server.py'}" webapp runserver "${{@: -1}}" ;;
esac
""")
    executable(bin_dir / "pg_isready", "#!/bin/bash\nexit ${FAKE_PG_RC:-0}\n")
    executable(bin_dir / "open", f'#!/bin/bash\necho "$@" >> "{tmp_path / "opened.txt"}"\n')
    port = free_port()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "UV": str(bin_dir / "uv"), "WEBAPP_PORT": str(port), "DASHBOARD_LOG_DIR": str(tmp_path / "logs")}
    yield {"env": env, "port": port, "tmp": tmp_path}
    subprocess.run(["bash", str(SCRIPT), "stop"], env=env, capture_output=True, text=True, timeout=30)     # never leave one running


def run(world, *args, **extra_env):
    return subprocess.run(["bash", str(SCRIPT), *args], env={**world["env"], **extra_env}, capture_output=True, text=True, timeout=60)


def listening(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def opened(world) -> list[str]:
    f = world["tmp"] / "opened.txt"
    return f.read_text().split() if f.exists() else []


def test_start_status_start_again_and_stop(world):
    started = run(world)
    assert started.returncode == 0, started.stdout + started.stderr
    assert f"Running at http://localhost:{world['port']}/dashboard/" in started.stdout and listening(world["port"])
    assert opened(world) == [f"http://localhost:{world['port']}/dashboard/"]
    status = run(world, "status")
    assert status.returncode == 0 and "Running at" in status.stdout and "Starting development server" in status.stdout
    again = run(world, "start", "--no-open")
    assert again.returncode == 0 and "Already running" in again.stdout and len(opened(world)) == 1
    stopped = run(world, "stop")
    assert stopped.returncode == 0 and "Stopped." in stopped.stdout
    time.sleep(0.3)
    assert not listening(world["port"])
    assert run(world, "status").returncode == 1 and "Not running" in run(world, "status").stdout
    assert "isn't running" in run(world, "stop").stdout
    log = (world["tmp"] / "logs" / "webapp.log").read_text()
    assert "starting on 127.0.0.1" in log and "stopped" in log


def test_no_open_and_restart(world):
    assert run(world, "--no-open").returncode == 0 and opened(world) == []
    restarted = run(world, "restart", "--no-open")
    assert restarted.returncode == 0 and "Stopped." in restarted.stdout and "Running at" in restarted.stdout and listening(world["port"])


def test_open_starts_it_if_needed(world):
    assert run(world, "open").returncode == 0 and listening(world["port"]) and len(opened(world)) == 1


def test_postgresql_not_running_says_how_to_start_it(world):
    result = run(world, FAKE_PG_RC="2")
    assert result.returncode == 1 and "brew services start postgresql@18" in result.stdout and not listening(world["port"])


def test_a_port_used_by_another_program_is_named(world):
    class Quiet(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
    other = HTTPServer(("127.0.0.1", world["port"]), Quiet)
    try:
        result = run(world)
        assert result.returncode == 1 and f"Port {world['port']} is used by another program" in result.stdout and "WEBAPP_PORT=8061" in result.stdout
    finally:
        other.server_close()


def test_a_crash_while_starting_shows_the_log(world):
    result = run(world, FAKE_CRASH="1")
    assert result.returncode == 1 and "stopped while starting" in result.stdout and "Error: something broke" in result.stdout


def test_waiting_migrations_are_mentioned_not_applied(world):
    result = run(world, "--no-open", FAKE_MIGRATE_RC="1")
    assert result.returncode == 0 and "warehouse backup && uv run python -m webapp migrate" in result.stdout and "Running at" in result.stdout


def test_help_and_unknown_words():
    env = {**os.environ}
    helped = subprocess.run(["bash", str(SCRIPT), "help"], env=env, capture_output=True, text=True, timeout=30)
    assert helped.returncode == 0 and "./dashboard.sh stop" in helped.stdout and "WEBAPP_PORT" in helped.stdout
    wrong = subprocess.run(["bash", str(SCRIPT), "launch"], env=env, capture_output=True, text=True, timeout=30)
    assert wrong.returncode == 2 and "Unknown: launch" in wrong.stdout


def test_it_listens_on_this_computer_only():
    text = SCRIPT.read_text()
    assert 'HOST="127.0.0.1"' in text and "0.0.0.0" not in text


def test_a_web_app_started_by_hand_is_recognised_not_called_another_program(world):
    """runserver 8060 typed in a Terminal: same app, but not this script's to stop."""
    long_dir = world["tmp"] / ("a-long-folder-name-" * 6)          # a long path must not hide what it is
    long_dir.mkdir()
    (long_dir / "server.py").write_text(FAKE_SERVER)
    by_hand = subprocess.Popen([sys.executable, str(long_dir / "server.py"), "webapp", "runserver", str(world["port"])])
    try:
        for _ in range(50):
            if listening(world["port"]):
                break
            time.sleep(0.1)
        started = run(world)
        assert started.returncode == 0 and "started another way" in started.stdout and len(opened(world)) == 1
        assert run(world, "status").returncode == 0 and "started another way" in run(world, "status").stdout
        stopping = run(world, "stop")
        assert stopping.returncode == 1 and "Ctrl+C" in stopping.stdout and f"kill {by_hand.pid}" in stopping.stdout
        assert by_hand.poll() is None                     # still running: it wasn't ours to stop
    finally:
        by_hand.terminate()
        by_hand.wait(10)
