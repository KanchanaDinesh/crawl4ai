"""A short vertical video (1080x1920) of the day's facts, drawn frame by frame with Pillow and
joined into an MP4 by ffmpeg. Everything runs on this computer: no AI, no tokens.

Scenes: title, today's numbers, a 3-month chart, news, tech launches, vehicle prices, the ending.
A part with no data is left out and the video gets shorter. A held-back day change (see facts.py)
shows only the level.
"""

import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .summary import CATEGORY

W, H = 1080, 1920
BG, PANEL, LINE = (14, 22, 33), (24, 37, 51), (40, 56, 74)
FG, MUTED = (236, 241, 246), (150, 166, 184)
BLUE, GOLD, GREEN, RED = (42, 171, 238), (240, 197, 91), (79, 209, 139), (255, 122, 112)
AVENIR = "/System/Library/Fonts/Avenir Next.ttc"          # macOS; elsewhere Pillow's own font is used


class NoFfmpeg(RuntimeError):
    pass


def _font_index(style: str) -> int | None:
    for i in range(16):
        try:
            f = ImageFont.truetype(AVENIR, 20, index=i)
        except OSError:
            return None
        if " ".join(f.getname()).endswith(" " + style):
            return i
    return None


def _font(size: int, style: str = "Bold"):
    idx = _font_index(style) if Path(AVENIR).exists() else None
    if idx is not None:
        return ImageFont.truetype(AVENIR, size, index=idx)
    return ImageFont.load_default(size=size)


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def mix(c, a):
    return tuple(int(BG[i] + (c[i] - BG[i]) * a) for i in range(3))


def updown(v: float) -> str:
    return f"{'up' if v >= 0 else 'down'} {abs(v):.2f}%"


class Scene:
    seconds = 4.5

    def __init__(self, video, start):
        self.v, self.start, self.end = video, start, start + self.seconds

    def alpha(self, t):
        return max(0.0, min(1.0, (t - self.start) / 0.45, (self.end - t) / 0.45))

    def p(self, t, delay=0.0, dur=0.7):
        return ease((t - self.start - delay) / dur)

    def rise(self, t, delay=0.0):
        return int((1 - self.p(t, delay)) * 46)

    def text(self, d, xy, s, f, color, a, anchor="la"):
        d.text(xy, s, font=self.v.f[f], fill=mix(color, a), anchor=anchor)


class Intro(Scene):
    seconds = 3.5

    def draw(self, d, t):
        a = self.alpha(t)
        for i, (line, col) in enumerate((("Markets.", FG), ("News.", FG), ("Tech. Cars.", FG))):
            self.text(d, (72, 640 + i * 140 + self.rise(t, i * 0.25)), line, "hero", col, a * self.p(t, i * 0.25))
        self.text(d, (72, 1110 + self.rise(t, 0.9)), "One short read every morning.", "body", GOLD, a * self.p(t, 0.9))


class Markets(Scene):
    seconds = 5.5

    def draw(self, d, t):
        a, m = self.alpha(t), self.v.facts["markets"]
        self.text(d, (72, 300 + self.rise(t)), "Today's numbers", "h1", FG, a * self.p(t))
        tiles = [s for s in ("ASPI", "GC=F", "LKR=X", "GOLD24K_LKR_PAWN") if s in m]
        for i, sym in enumerate(tiles):
            q, k = m[sym], self.p(t, 0.3 + i * 0.25)
            x, y = 72, 470 + i * 270 + int((1 - k) * 46)
            value = f"Rs {q['price']:,.0f}" if sym == "GOLD24K_LKR_PAWN" else f"{q['price']:,.2f}"
            if q["held_back"] or q["change_pct"] is None:
                sub, col = ("checking the change" if q["held_back"] else ""), MUTED
            else:
                sub, col = updown(q["change_pct"]), (GREEN if q["change_pct"] >= 0 else RED)
            d.rounded_rectangle([x, y, W - 72, y + 230], 28, fill=mix(PANEL, a * k))
            self.text(d, (x + 40, y + 34), q["label"], "small", MUTED, a * k)
            self.text(d, (x + 40, y + 84), value, "big", FG, a * k)
            self.text(d, (W - 112, y + 112), sub, "body", col, a * k, "ra")
        self.text(d, (72, 1580), "Information only, not financial advice.", "small", MUTED, a * self.p(t, 1.4))


class Chart(Scene):
    seconds = 5.0

    def series(self):
        m = self.v.facts["markets"]
        out = []
        for sym, col in (("^GSPC", BLUE), ("GC=F", GOLD)):
            s = m.get(sym, {}).get("three_months") or []
            if len(s) >= 20:
                out.append((m[sym]["label"].split(",")[0], col, [(c / s[0][1] - 1) * 100 for _, c in s], s[0][0]))
        return out

    def draw(self, d, t):
        a, series = self.alpha(t), self.series()
        self.text(d, (72, 300 + self.rise(t)), "3 months, side by side", "h1", FG, a * self.p(t))
        start = series[0][3]
        self.text(d, (72, 400 + self.rise(t, 0.15)), f"% change since {start[8:]}/{start[5:7]}", "body", MUTED,
                  a * self.p(t, 0.15))
        x0, x1, y0, y1 = 120, W - 90, 560, 1320
        allv = [v for _, _, s, _ in series for v in s] + [0]
        lo, hi = min(allv), max(allv)
        pad = (hi - lo) * 0.1 or 1
        lo, hi = lo - pad, hi + pad
        step = 5 if hi - lo > 12 else 2
        Y = lambda v: y1 - (v - lo) / (hi - lo) * (y1 - y0)  # noqa: E731
        for v in range(math.ceil(lo / step) * step, int(hi) + 1, step):
            d.line([(x0, Y(v)), (x1, Y(v))], fill=mix(LINE, a), width=2)
            self.text(d, (x0 - 16, Y(v)), f"{v:+d}%" if v else "0%", "tag", MUTED, a, "rm")
        k = ease((t - self.start - 0.5) / 2.6)
        for _, col, s, _ in series:
            n = max(2, int(len(s) * k))
            pts = [(x0 + i / (len(s) - 1) * (x1 - x0), Y(v)) for i, v in enumerate(s[:n])]
            d.line(pts, fill=mix(col, a), width=7, joint="curve")
            ex, ey = pts[-1]
            d.ellipse([ex - 11, ey - 11, ex + 11, ey + 11], fill=mix(col, a))
        for i, (name, col, s, _) in enumerate(series):
            self.text(d, (72, 1400 + i * 80), f"{name}  {s[-1]:+.1f}%", "h2", col, a * self.p(t, 3.0))
        brent = self.v.facts["markets"].get("BZ=F")
        if brent and brent["change_pct"] is not None and not brent["held_back"]:
            self.text(d, (72, 1580), f"Brent oil today: {updown(brent['change_pct'])}", "body",
                      GREEN if brent["change_pct"] >= 0 else RED, a * self.p(t, 3.4))


class News(Scene):
    seconds = 4.5

    def draw(self, d, t):
        a, n = self.alpha(t), self.v.facts["news"]
        k = ease((t - self.start - 0.3) / 1.6)
        self.text(d, (72, 560 + self.rise(t)), f"{int(n['week_headlines'] * k):,}", "hero", FG, a)
        self.text(d, (72, 700 + self.rise(t)), "headlines this week", "h2", MUTED, a)
        self.text(d, (72, 880 + self.rise(t, 0.3)), f"{int(n['week_sources'] * k)}", "hero", FG, a * self.p(t, 0.3))
        self.text(d, (72, 1020 + self.rise(t, 0.3)), "news sources around the world", "h2", MUTED, a * self.p(t, 0.3))
        self.text(d, (72, 1220 + self.rise(t, 1.4)), "We read them all.", "h1", GOLD, a * self.p(t, 1.4))
        self.text(d, (72, 1320 + self.rise(t, 1.7)), "You get the 3 that matter.", "h1", FG, a * self.p(t, 1.7))


class Tech(Scene):
    seconds = 4.0

    def draw(self, d, t):
        a, tech = self.alpha(t), self.v.facts["tech"]
        self.text(d, (72, 330 + self.rise(t)), str(tech["launches"]), "hero", GREEN, a * self.p(t))
        self.text(d, (72, 470 + self.rise(t)), "new tech products this week,", "h2", FG, a * self.p(t))
        self.text(d, (72, 540 + self.rise(t)), "on domains under 7 days old", "h2", FG, a * self.p(t))
        cats = tech["launches_by_category"][:5]
        top = max(n for _, n in cats)
        for i, (cat, n) in enumerate(cats):
            k = self.p(t, 0.6 + i * 0.18, 0.9)
            y = 720 + i * 150
            self.text(d, (72, y), CATEGORY.get(cat, cat), "body", MUTED, a * min(1, k * 2))
            bw = max(8, int((W - 234) * n / top * k))
            d.rounded_rectangle([72, y + 58, 72 + bw, y + 108], 14, fill=mix(GREEN, a))
            self.text(d, (72 + bw + 20, y + 83), str(n), "h2", FG, a * min(1, k * 2), "lm")
        self.text(d, (72, 1520), "Found from certificate logs and Hacker News.", "small", MUTED, a * self.p(t, 1.6))


class Cars(Scene):
    seconds = 3.5

    def draw(self, d, t):
        a, v = self.alpha(t), self.v.facts["vehicles"]
        makes = [m for m in v["makes"] if m[2]]
        self.text(d, (72, 330 + self.rise(t)), "Vehicle market", "h1", FG, a * self.p(t))
        self.text(d, (72, 430 + self.rise(t)), f"median asking price · {v['ads']} ads tracked", "body", MUTED,
                  a * self.p(t))
        top = max(p for _, _, p in makes)
        for i, (make, n, price) in enumerate(makes):
            k = self.p(t, 0.4 + i * 0.2, 0.9)
            y = 600 + i * 220
            self.text(d, (72, y), make, "h2", FG, a * min(1, k * 2))
            self.text(d, (W - 72, y + 8), f"{n} ads", "small", MUTED, a * min(1, k * 2), "ra")
            bw = max(8, int((W - 404) * price / top * k))
            d.rounded_rectangle([72, y + 80, 72 + bw, y + 140], 16, fill=mix(GOLD, a))
            self.text(d, (72 + bw + 22, y + 110), f"Rs {price / 1e6:.2f}M", "h2", FG, a * min(1, k * 2), "lm")
        self.text(d, (72, 1520), "From ikman.lk and riyasewana.com ads.", "small", MUTED, a * self.p(t, 1.4))


class Outro(Scene):
    seconds = 4.0

    def alpha(self, t):
        return max(0.0, min(1.0, (t - self.start) / 0.45))

    def draw(self, d, t):
        a = self.alpha(t)
        self.text(d, (72, 640 + self.rise(t)), "Every morning", "hero", FG, a * self.p(t))
        self.text(d, (72, 780 + self.rise(t, 0.2)), "at 7:30.", "hero", GOLD, a * self.p(t, 0.2))
        k = self.p(t, 0.6)
        d.rounded_rectangle([72, 1000, W - 72, 1150], 75, fill=mix(BLUE, a * k))
        self.text(d, (W // 2, 1075), "Join free on Telegram", "h2", (255, 255, 255), a * k, "mm")
        self.text(d, (W // 2, 1230), self.v.link, "body", MUTED, a * self.p(t, 0.9), "ma")
        self.text(d, (W // 2, 1600), "Information only, not financial advice.", "small", MUTED, a * self.p(t, 1.1), "ma")


class Video:
    def __init__(self, facts: dict, title: str = "SRI LANKA DAILY INTELLIGENCE", link: str = "[t.me/your-channel]"):
        self.facts, self.title, self.link = facts, title, link
        self.f = {"hero": _font(118, "Heavy"), "h1": _font(76, "Bold"), "h2": _font(54, "Demi Bold"),
                  "big": _font(92, "Bold"), "body": _font(40, "Medium"), "small": _font(32, "Medium"),
                  "tag": _font(28, "Demi Bold")}
        self.scenes, t = [], 0.0
        for cls in (Intro, Markets, Chart, News, Tech, Cars, Outro):
            if self._has(cls):
                self.scenes.append(cls(self, t))
                t += cls.seconds
        self.seconds = t

    def _has(self, cls) -> bool:
        f = self.facts
        if cls is Markets:
            return bool(f.get("markets"))
        if cls is Chart:
            return bool(f.get("markets")) and len(Chart(self, 0).series()) > 0
        if cls is News:
            return bool(f.get("news")) and f["news"]["week_headlines"] > 0
        if cls is Tech:
            return bool(f.get("tech")) and f["tech"]["launches"] > 0
        if cls is Cars:
            return bool(f.get("vehicles")) and any(m[2] for m in f["vehicles"]["makes"])
        return True

    def frame(self, t: float) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        for y in range(0, H, 120):                       # faint lines, like chart paper
            d.line([(0, y), (W, y)], fill=(18, 28, 41), width=1)
        d.text((72, 96), self.title, font=self.f["tag"], fill=BLUE)
        d.text((W - 72, 96), self.facts["date_label"].upper(), font=self.f["tag"], fill=MUTED, anchor="ra")
        d.rectangle([72, H - 96, W - 72, H - 90], fill=PANEL)
        d.rectangle([72, H - 96, 72 + int((W - 144) * min(1, t / self.seconds)), H - 90], fill=BLUE)
        for sc in self.scenes:
            if sc.start <= t <= sc.end:
                sc.draw(d, t)
        return img

    def render(self, out: Path, fps: int = 30) -> Path:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise NoFfmpeg("ffmpeg is needed to make the video. Install it once with: brew install ffmpeg")
        out.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                 "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                                 "-crf", "20", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
        for i in range(int(self.seconds * fps)):
            proc.stdin.write(self.frame(i / fps).tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg could not write {out}")
        return out
