"""doctor.py advice, the browser-location switch, and recognising a dead browser."""

import importlib
import os

import pytest


def test_browsers_on_system_drive_switch(tmp_path, monkeypatch):
    import project_paths
    monkeypatch.setattr(project_paths, "REPO_ROOT", tmp_path)
    for text, expected in [("BROWSERS_ON_SYSTEM_DRIVE=0\n", False), ("BROWSERS_ON_SYSTEM_DRIVE=1\n", True),
                           ('BROWSERS_ON_SYSTEM_DRIVE="1"  # exFAT drive\n', True), ("BROWSERS_ON_SYSTEM_DRIVE=10\n", False),
                           ("# BROWSERS_ON_SYSTEM_DRIVE=1\n", False)]:
        (tmp_path / "automation.conf").write_text(text)
        assert project_paths.browsers_on_system_drive() is expected, text
    (tmp_path / "automation.conf").write_text("BROWSERS_ON_SYSTEM_DRIVE=1\n")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("PROJECT_LOCAL_DIR", str(tmp_path / ".local"))
    project_paths.apply_local_env()
    assert "ms-playwright" in os.environ["PLAYWRIGHT_BROWSERS_PATH"] and ".local" not in os.environ["PLAYWRIGHT_BROWSERS_PATH"]


def test_dead_browser_is_recognised():
    from vehicles.scrape import _browser_died
    from techintel.fetch import browser_died, friendly_error
    msg = "BrowserContext.new_page: Target page, context or browser has been closed"
    assert browser_died(msg) and _browser_died(msg) and not browser_died("Request timed out")
    assert friendly_error(msg).startswith("browser closed unexpectedly")


@pytest.mark.parametrize("fs, links, expect", [("exfat", False, "BROWSERS_ON_SYSTEM_DRIVE=1"),
                                               ("apfs", True, "All good")])
def test_doctor_advice(fs, links, expect, monkeypatch, capsys, tmp_path):
    import doctor
    importlib.reload(doctor)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    (tmp_path / "chromium-1248").mkdir()
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor, "filesystem_of", lambda p: fs)
    monkeypatch.setattr(doctor, "supports_symlinks", lambda p: links)

    async def browser_ok():
        return None if links else "BrowserContext.new_page: Target page, context or browser has been closed"

    async def crawl_ok(stealth, vehicles_settings=False):
        return await browser_ok()
    monkeypatch.setattr(doctor, "try_playwright", browser_ok)
    monkeypatch.setattr(doctor, "try_crawl4ai", crawl_ok)
    monkeypatch.setattr(doctor, "try_graphics_page", lambda args: browser_ok())
    if links:
        doctor.main()
    else:
        with pytest.raises(SystemExit):
            doctor.main()
    assert expect in capsys.readouterr().out


def test_chromium_args_default_and_override(tmp_path, monkeypatch):
    import project_paths
    monkeypatch.setattr(project_paths, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("CHROMIUM_ARGS", raising=False)
    assert project_paths.chromium_args() == ["--in-process-gpu"]          # no automation.conf: the default
    (tmp_path / "automation.conf").write_text('CHROMIUM_ARGS="--in-process-gpu --disable-gpu-sandbox"\n')
    assert project_paths.chromium_args() == ["--in-process-gpu", "--disable-gpu-sandbox"]
    (tmp_path / "automation.conf").write_text('CHROMIUM_ARGS=""\n')
    assert project_paths.chromium_args() == []
    monkeypatch.setenv("CHROMIUM_ARGS", "--foo")                          # the environment wins
    assert project_paths.chromium_args() == ["--foo"]


@pytest.mark.parametrize("with_flags, plain, expect", [
    (None, "FATAL: GPU process isn't usable. Goodbye.", "All good"),       # the flag fixes it: say so
    ("FATAL: GPU process isn't usable. Goodbye.", "FATAL: GPU process isn't usable. Goodbye.", "--disable-gpu-sandbox"),
])
def test_doctor_graphics_page(with_flags, plain, expect, monkeypatch, capsys, tmp_path):
    import doctor
    importlib.reload(doctor)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.delenv("CHROMIUM_ARGS", raising=False)
    (tmp_path / "chromium-1248").mkdir()
    monkeypatch.setattr(doctor, "filesystem_of", lambda p: "apfs")
    monkeypatch.setattr(doctor, "supports_symlinks", lambda p: True)

    async def ok(*a, **k):
        return None

    async def graphics(args):
        return with_flags if args else plain
    monkeypatch.setattr(doctor, "try_playwright", ok)
    monkeypatch.setattr(doctor, "try_crawl4ai", ok)
    monkeypatch.setattr(doctor, "try_graphics_page", graphics)
    if with_flags:
        with pytest.raises(SystemExit):
            doctor.main()
    else:
        doctor.main()
    out = capsys.readouterr().out
    assert expect in out and "graphics page" in out
    if not with_flags:
        assert "same page without those flags: fails" in out


def test_symlink_and_filesystem_probes(tmp_path):
    import doctor
    assert doctor.supports_symlinks(tmp_path) is True
    assert isinstance(doctor.filesystem_of(tmp_path), str)
