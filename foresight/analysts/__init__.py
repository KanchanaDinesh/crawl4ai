"""One analyst per section. A new dataset means one new file here: the framework, the scoring and the page need no change.

An analyst's jobs (remember and progress are optional):
    load(con)                 read what it needs, with the read-only login it's given
    signals(data, today)      what's going on now
    forecast(data, today)     scenarios with odds and reasons, and how many weren't made and why
    check(row, data)          what really happened to a saved forecast, or None if it's too early to say
    test(data)                how the method would have done on the past, using only what was known each day
    remember(data, store)     save what can't be worked out again later
    progress(data)            how far it is from giving odds, in plain words
"""

from .markets import MarketsAnalyst
from .news import NewsAnalyst

ANALYSTS = {"markets": MarketsAnalyst, "news": NewsAnalyst}
