"""A template's own CSS, typed by whoever makes it.

It reaches a PDF and pages other people open, so it's never used as typed. It's parsed, checked, and what's used is the parsed rules written out
again, each one tied to the report (`.r-doc ...`), so comments, odd escapes and tricks in the text don't survive. The rules: nothing may fetch
anything (no url(), @import or @font-face), and only ordinary style rules and @page. The same idea as the AI assistant's query check (`assistant/sqlcheck.py`)."""

import tinycss2

MAX_LENGTH = 20_000
SCOPE = ".r-doc"

# Named, rather than refused one by one: new CSS functions that fetch things keep appearing (image-set, cross-fade, element).
VALUE_FUNCTIONS = {"rgb", "rgba", "hsl", "hsla", "hwb", "lab", "lch", "oklab", "oklch", "color-mix", "calc", "min", "max", "clamp", "var", "attr",
                   "linear-gradient", "radial-gradient", "conic-gradient", "repeating-linear-gradient", "repeating-radial-gradient",
                   "counter", "counters", "string", "target-counter", "leader",
                   "translate", "translatex", "translatey", "scale", "scalex", "scaley", "rotate", "skew", "skewx", "skewy", "matrix", "repeat", "minmax", "fit-content"}
SELECTOR_FUNCTIONS = {"not", "is", "where", "has", "lang", "nth-child", "nth-last-child", "nth-of-type", "nth-last-of-type"}
BANNED_PROPERTIES = {"behavior", "-moz-binding", "-ms-behavior"}
MARGIN_BOXES = {f"{side}-{where}" for side, wheres in (("top", ("left-corner", "left", "center", "right", "right-corner")), ("bottom", ("left-corner", "left", "center", "right", "right-corner")),
                                                       ("left", ("top", "middle", "bottom")), ("right", ("top", "middle", "bottom"))) for where in wheres}
FETCHING = "Line {line}: nothing in a report may come from an address, so url() can't be used. For a logo, use the Logo part of this form."


class CSSProblem(ValueError):
    """What to tell the person."""


def _check_tokens(tokens, allowed: set[str]) -> None:
    for token in tokens or []:
        if token.type == "url":
            raise CSSProblem(FETCHING.format(line=token.source_line))
        if token.type == "function":
            if token.lower_name == "url":
                raise CSSProblem(FETCHING.format(line=token.source_line))
            if token.lower_name not in allowed:
                raise CSSProblem(f"Line {token.source_line}: {token.name}() can't be used in a report's CSS.")
            _check_tokens(token.arguments, allowed)
        elif token.type in ("() block", "[] block", "{} block"):
            _check_tokens(token.content, allowed)
        elif token.type == "error":
            raise CSSProblem(f"Line {token.source_line}: {token.message}. Please check the CSS there.")


def _declarations(contents) -> list[str]:
    out = []
    for item in contents:
        if item.type == "error":
            raise CSSProblem(f"Line {item.source_line}: {item.message}. Please check the CSS there.")
        if item.type != "declaration":
            raise CSSProblem(f"Line {item.source_line}: only properties and values go inside a rule, not another rule.")
        if item.lower_name in BANNED_PROPERTIES:
            raise CSSProblem(f"Line {item.source_line}: {item.name} can't be used in a report's CSS.")
        _check_tokens(item.value, VALUE_FUNCTIONS)
        out.append(item.serialize().strip())
    return out


def _parts(contents):
    return tinycss2.parse_blocks_contents(contents, skip_comments=True, skip_whitespace=True)


def _scoped_selectors(rule) -> str:
    """Each selector, tied to the report. The comma inside :not(a, b) is part of its function, so splitting on the top-level ones is safe."""
    _check_tokens(rule.prelude, SELECTOR_FUNCTIONS)
    selectors, current = [], []
    for token in rule.prelude:
        if token.type == "literal" and token.value == ",":
            selectors.append(current)
            current = []
        else:
            current.append(token)
    selectors.append(current)
    written = []
    for tokens in selectors:
        text = tinycss2.serialize(tokens).strip()
        if not text:
            raise CSSProblem(f"Line {rule.source_line}: a selector is empty. Check for a stray comma.")
        # `.r-doc` followed by a letter, digit, - or _ is a different class that only starts the same, and must be tied like any other
        own = text == SCOPE or (text.startswith(SCOPE) and not (text[len(SCOPE)].isalnum() or text[len(SCOPE)] in "-_\\"))
        written.append(text if own else f"{SCOPE} {text}")
    return ", ".join(written)


def _page_rule(rule) -> str:
    """@page can set margins, size and what's printed in the page margins."""
    _check_tokens(rule.prelude, set())
    inner = []
    for item in _parts(rule.content or []):
        if item.type == "at-rule":
            if item.lower_at_keyword not in MARGIN_BOXES:
                raise CSSProblem(f"Line {item.source_line}: @{item.at_keyword} can't go inside @page.")
            inner.append(f"@{item.lower_at_keyword} {{ {'; '.join(_declarations(_parts(item.content or [])))} }}")
        else:
            inner.extend(_declarations([item]))
    prelude = tinycss2.serialize(rule.prelude).strip()
    return f"@page {prelude} {{ {'; '.join(inner)} }}" if prelude else f"@page {{ {'; '.join(inner)} }}"


def clean(text: str) -> str:
    """The CSS to use, written out again; empty if there's none. Raises CSSProblem saying what to change."""
    if not (text or "").strip():
        return ""
    if len(text) > MAX_LENGTH:
        raise CSSProblem(f"That's over {MAX_LENGTH:,} characters of CSS. Please shorten it.")
    if "<" in text:
        raise CSSProblem("CSS has no use for the < sign, so it isn't accepted. Write only CSS rules here, with no HTML tags.")
    written = []
    for rule in tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True):
        if rule.type == "error":
            raise CSSProblem(f"Line {rule.source_line}: {rule.message}. Please check the CSS there.")
        if rule.type == "at-rule":
            if rule.lower_at_keyword != "page":
                raise CSSProblem(f"Line {rule.source_line}: @{rule.at_keyword} can't be used. Only ordinary rules and @page can, because a report fetches nothing from outside.")
            written.append(_page_rule(rule))
        else:
            written.append(f"{_scoped_selectors(rule)} {{ {'; '.join(_declarations(_parts(rule.content)))} }}")
    out = "\n".join(written)
    if "<" in out:              # an escape in the text (\3c) comes out here as the real sign, which could end the style block
        raise CSSProblem("CSS has no use for the < sign, even written as an escape, so it isn't accepted.")
    return out
