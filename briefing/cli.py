"""python -m briefing <command>     (everything offline: no AI, no tokens, no internet)

    run       facts + text summary + video for today, saved in output/briefing/   (default)
    summary   print today's text summary
    facts     print today's facts as JSON
    video     only the video   [--fps 30] [--out FILE]

Options for run and video: --title "SRI LANKA DAILY INTELLIGENCE", --link "t.me/your-channel"
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import project_paths

from . import facts as facts_mod
from . import summary
from .video import NoFfmpeg, Video

logger = logging.getLogger("briefing")


def output_dir() -> Path:
    return Path(os.environ.get("SCRAPERS_OUTPUT_DIR", project_paths.REPO_ROOT / "output")) / "briefing"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m briefing", description="Daily briefing: facts, text summary, video.")
    sub = p.add_subparsers(dest="command")
    for name in ("run", "video"):
        s = sub.add_parser(name)
        s.add_argument("--title", default="SRI LANKA DAILY INTELLIGENCE")
        s.add_argument("--link", default="[t.me/your-channel]", help="Shown at the end of the video")
        s.add_argument("--fps", type=int, default=30)
        if name == "video":
            s.add_argument("--out", help="Video file (default output/briefing/video_<date>.mp4)")
    sub.add_parser("summary")
    sub.add_parser("facts")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    command = args.command or "run"

    facts = facts_mod.collect()
    if command == "facts":
        print(json.dumps(facts, indent=2, default=float))
        return
    text = summary.build(facts)
    if command == "summary":
        print(text)
        return

    out = output_dir()
    out.mkdir(parents=True, exist_ok=True)
    day = facts["date"]
    if command == "run":
        (out / f"facts_{day}.json").write_text(json.dumps(facts, indent=2, default=float), encoding="utf-8")
        (out / f"summary_{day}.txt").write_text(text, encoding="utf-8")
        logger.info("Saved %s and %s", out / f"summary_{day}.txt", out / f"facts_{day}.json")
    held = [q["label"] for q in (facts.get("markets") or {}).values() if q["held_back"]]
    if held:
        logger.warning("Day change held back (unusual, needs a second source): %s", ", ".join(held))
    target = Path(getattr(args, "out", None) or out / f"video_{day}.mp4")
    try:
        video = Video(facts, title=args.title, link=args.link)
        logger.info("Rendering a %.0f-second video ...", video.seconds)
        video.render(target, fps=args.fps)
    except NoFfmpeg as e:
        logger.error("%s", e)
        sys.exit(1)
    logger.info("Saved %s (%d KB)", target, target.stat().st_size // 1024)
    if command == "run":
        print("\n" + text)


if __name__ == "__main__":
    main()
