"""The daily briefing: facts from the sample databases, the text summary, the held-back check, the video."""

import importlib
import shutil

import pytest

from sample_data import build_markets, build_news, build_tech


@pytest.fixture
def facts(tmp_path, monkeypatch, pg_env):
    from warehouse import postgres
    postgres.setup()                                   # news lives in the test PostgreSQL
    monkeypatch.setenv("PROJECT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SCRAPERS_OUTPUT_DIR", str(tmp_path / "out"))
    for var in ("WORLDNEWS_DATA_DIR", "TECHINTEL_DATA_DIR", "MARKETS_DATA_DIR", "VEHICLES_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    import project_paths
    import warehouse
    for mod in (project_paths, warehouse):
        importlib.reload(mod)
    from worldnews.settings import load_settings
    build_news(warehouse.DB_FILES["news"].parent, load_settings().raw["tech_keywords"])
    build_tech(warehouse.DB_FILES["tech"].parent)
    build_markets(warehouse.DB_FILES["markets"].parent)
    from briefing import facts as facts_mod
    importlib.reload(facts_mod)
    return facts_mod.collect()


def test_facts_come_from_every_database(facts):
    m = facts["markets"]
    assert {"ASPI", "GC=F", "LKR=X", "GOLD24K_LKR_PAWN", "^GSPC"} <= set(m)
    assert len(m["^GSPC"]["three_months"]) >= 20 and m["^GSPC"]["change_3m"] > 0     # sample rises 0.1% a day
    assert facts["news"]["week_headlines"] > 0 and facts["tech"]["launches"] >= 0
    assert facts["vehicles"] is None                                                   # no vehicles database here


def test_an_unusual_move_is_held_back():
    from briefing.facts import unusual
    calm = [["d%d" % i, 100.2 if i % 2 else 100.0] for i in range(60)]                # daily moves of about 0.2%
    assert unusual(3.1, calm)                                                         # like USD/LKR +3.1%: held back
    assert not unusual(0.5, calm)
    assert not unusual(3.1, calm[:10])                                                # too little history to judge


def test_summary_has_no_advice_and_holds_back_the_unusual(facts):
    from briefing import summary
    facts["markets"]["LKR=X"]["held_back"] = True
    text = summary.build(facts)
    assert text.startswith("Morning Brief") and "Information only, not financial advice." in text
    assert "USD / LKR" in text and "held back" in text and "VEHICLES" not in text
    assert not any(w in text.lower() for w in (" buy", " sell", "will rise", "will fall", "guaranteed"))


def test_video_frames_and_render(facts, tmp_path):
    from briefing.video import Video
    v = Video(facts, link="t.me/example")
    assert [type(s).__name__ for s in v.scenes][:3] == ["Intro", "Markets", "Chart"] and "Cars" not in str(v.scenes)
    assert v.frame(5.0).size == (1080, 1920) and v.frame(v.seconds - 0.1).size == (1080, 1920)
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = v.render(tmp_path / "v.mp4", fps=4)
    assert out.exists() and out.stat().st_size > 10_000
