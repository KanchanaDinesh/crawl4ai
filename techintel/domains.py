"""Domain helpers: root domain extraction and filtering."""

import ipaddress
from urllib.parse import urlsplit

import tldextract

# Use the bundled public-suffix list only: no network call, same result every run.
_extract = tldextract.TLDExtract(suffix_list_urls=())


def host_of(value: str) -> str:
    """Host (with port, if any) from a URL or a bare domain."""
    value = value.strip().lower()
    if "://" not in value:
        value = "http://" + value
    return urlsplit(value).netloc.split("@")[-1].lstrip("*.").rstrip(".")


def _is_local(host: str) -> bool:
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name.strip("[]"))
        return True
    except ValueError:
        return False


def root_domain(value: str) -> str | None:
    """'https://api.shop.foo.com.lk/x' -> 'foo.com.lk'. IPs/localhost keep host:port."""
    host = host_of(value)
    if not host:
        return None
    if _is_local(host):
        return host
    result = _extract(host.split(":")[0])
    return result.top_domain_under_public_suffix or None


def homepage_url(domain: str) -> str:
    scheme = "http" if _is_local(domain) else "https"
    return f"{scheme}://{domain}/"


class DomainFilter:
    """Decides which domains are worth keeping, based on settings['domain_filter']."""

    def __init__(self, config: dict):
        self.match_tlds = tuple(t.lower() for t in config.get("match_tlds", []))
        self.match_keywords = tuple(k.lower() for k in config.get("match_keywords", []))
        self.ignore_domains = {d.lower() for d in config.get("ignore_domains", [])}
        self.ignore_suffixes = tuple(s.lower() for s in config.get("ignore_suffixes", []))

    def is_ignored(self, value: str) -> bool:
        host = host_of(value)
        root = root_domain(value) or host
        return (
            root in self.ignore_domains
            or host in self.ignore_domains
            or host.endswith(self.ignore_suffixes)
            # "x.vercel.app" reduces to the root "vercel.app": ignore the platform itself too
            or f".{root}" in self.ignore_suffixes
            or f".{host}" in self.ignore_suffixes
        )

    def is_interesting_name(self, domain: str) -> bool:
        """Cheap name-only filter for firehose sources (certificate logs)."""
        domain = domain.lower()
        if domain.endswith(self.match_tlds):
            return True
        label = domain.split(".")[0]
        return any(k in label for k in self.match_keywords)
