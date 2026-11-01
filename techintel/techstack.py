"""Detect technologies a site uses from its HTML and response headers.

Rules live in config/fingerprints.json; each rule can match:
  html            list of regexes searched in the raw HTML
  headers         {header-name: regex} on response headers
  meta_generator  regex on <meta name="generator">
"""

import re


class TechDetector:
    def __init__(self, fingerprints: list[dict]):
        self.rules = []
        for fp in fingerprints:
            self.rules.append({
                "name": fp["name"],
                "group": fp.get("group", "other"),
                "html": [re.compile(p, re.I) for p in fp.get("html", [])],
                "headers": {k.lower(): re.compile(v, re.I) for k, v in fp.get("headers", {}).items()},
                "generator": re.compile(fp["meta_generator"], re.I) if fp.get("meta_generator") else None,
            })

    def detect(self, html: str, headers: dict | None = None, generator: str = "") -> list[dict]:
        headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        found = []
        for rule in self.rules:
            hit = (
                any(p.search(html) for p in rule["html"])
                or any(name in headers and p.search(headers[name]) for name, p in rule["headers"].items())
                or bool(rule["generator"] and generator and rule["generator"].search(generator))
            )
            if hit:
                found.append({"name": rule["name"], "group": rule["group"]})
        return found
