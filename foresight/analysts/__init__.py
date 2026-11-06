"""One analyst per section. A new dataset means one new file here: the framework, the scoring and the page need no change.

An analyst has four jobs:
    load(con)                 read what it needs, with the read-only login it's given
    signals(data, today)      what's going on now
    forecast(data, today)     scenarios with odds and reasons, and how many weren't made and why
    check(row, data)          what really happened to a saved forecast, or None if it's too early to say
    test(data)                how the method would have done on the past, using only what was known each day
"""

from .markets import MarketsAnalyst

ANALYSTS = {"markets": MarketsAnalyst}
