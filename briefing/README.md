# Daily briefing

The day's **text summary** (Morning Brief style) and a **30-second vertical video** (1080×1920, for Telegram and
phones), made from the project's own databases. Everything runs on this computer: **no AI, no tokens, no internet**.
Making one takes about 7 seconds.

```bash
uv run python -m briefing run        # summary + facts + video, saved in output/briefing/
uv run python -m briefing summary    # just print today's summary
uv run python -m briefing facts      # the numbers behind it, as JSON
uv run python -m briefing video --link "t.me/your-channel" --title "YOUR CHANNEL NAME"
./run_all.sh briefing                # the same as a run_all.sh step (it is in the default steps, last)
```

Files in `output/briefing/`: `summary_<date>.txt`, `facts_<date>.json`, `video_<date>.mp4`.

## How it works

- `facts.py` reads each database read-only: market prices and their saved daily history, this week's headlines
  and sources, new tech products on domains under 7 days old, and vehicle prices by make. A missing database is
  simply left out, and the video gets shorter.
- `summary.py` puts those numbers into fixed sentences. The wording never changes a number, and it never gives
  advice (no buy, sell, will rise). An AI writer could later rephrase the same facts; that would be optional.
- `video.py` draws each frame with Pillow (title, today's numbers, a 3-month chart drawing in, news, tech launches,
  vehicle prices, the ending) and joins them into an MP4 with ffmpeg. Needs ffmpeg once: `brew install ffmpeg`.
  The Avenir Next font is used on a Mac; elsewhere Pillow's own font.

## The "held back" check

A day's change more than 10 times the typical (median) daily move in the saved history is not shown: the post
shows only the level and says the change is waiting for a second source. Gold per pawn is held back too when the
dollar rate is, because it is worked out from it. Measured on 3 Nov 2026: USD/LKR +3.1% was 18 times its typical
move (a gap in Yahoo's history), while Brent oil's real −11% fall was 5.8 times, so Brent is shown.
