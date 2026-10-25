"""Fetch a donor URL. Tavily for public pages; plain HTTP for our own labeled demo page and as fallback."""
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from .config import Settings
from .textutil import html_to_text


@dataclass
class FetchResult:
    status: str  # "ok" | "gone" (404/410/empty) | "unreachable" (network/5xx)
    text: str = ""
    via: str = ""
    detail: str = ""


def is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


class Fetcher:
    def __init__(self, settings: Settings, http: Optional[httpx.Client] = None, tavily: Any = None):
        self.settings = settings
        self._http = http or httpx.Client(timeout=20, follow_redirects=False,
                                          headers={"User-Agent": "DonorLookback/0.1 (hackathon demo)"})
        self._tavily = tavily

    def _tavily_client(self):
        if self._tavily is None and self.settings.tavily_api_key:
            from tavily import TavilyClient
            self._tavily = TavilyClient(api_key=self.settings.tavily_api_key)
        return self._tavily

    def fetch(self, url: str) -> FetchResult:
        client = None if is_local_url(url) else self._tavily_client()
        if client is not None:
            try:
                resp = client.extract(urls=[url], format="text")
                for item in resp.get("results", []):
                    text = (item.get("raw_content") or "").strip()
                    if text:
                        return FetchResult("ok", text, "tavily")
                if resp.get("failed_results"):
                    return self._http_fetch(url, note="tavily could not extract")
                return FetchResult("gone", "", "tavily", "empty extraction")
            except Exception as exc:  # noqa: BLE001 - Tavily outage must not hide the page; fall back and say so
                return self._http_fetch(url, note=f"tavily error: {type(exc).__name__}")
        return self._http_fetch(url)

    def _get_following_redirects(self, url: str, hops: int = 4) -> httpx.Response:
        """Follow redirects by hand so every hop passes the same internal-host check as the first URL."""
        for _ in range(hops + 1):
            r = self._http.get(url)
            if r.status_code not in (301, 302, 303, 307, 308) or "location" not in r.headers:
                return r
            nxt = str(httpx.URL(url).join(r.headers["location"]))
            if donor_url_problem(nxt, self.settings.public_base_url):
                raise httpx.UnsupportedProtocol(f"redirect to a non-public address was refused: {nxt[:80]}")
            url = nxt
        raise httpx.TooManyRedirects("too many redirects")

    def _http_fetch(self, url: str, note: str = "") -> FetchResult:
        via = "http" if not note else "http (fallback)"
        try:
            r = self._get_following_redirects(url)
        except httpx.HTTPError as exc:
            return FetchResult("unreachable", "", via, f"{note} {type(exc).__name__}: {exc}".strip())
        if r.status_code in (404, 410):
            return FetchResult("gone", "", via, f"HTTP {r.status_code}")
        if r.status_code >= 400:
            return FetchResult("unreachable", "", via, f"HTTP {r.status_code}")
        ctype = r.headers.get("content-type", "")
        text = html_to_text(r.text) if "html" in ctype or r.text.lstrip().startswith("<") else r.text.strip()
        if not text:
            return FetchResult("gone", "", via, "empty page")
        return FetchResult("ok", text, via, note)


_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet")


def _resolves_internal(host: str) -> bool:
    """True if the host is, or resolves to, a loopback/private/link-local/reserved address. Unresolvable hosts are not
    blocked here (the fetch will simply fail); numeric forms like 127.1 are normalised by the resolver."""
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return True
    return False


def donor_url_problem(url: str, public_base_url: str) -> str:
    """Empty string if the URL may be registered as a donor. Internal hosts are refused unless they are our own demo host."""
    try:
        parsed = urlparse(url)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return "Donor URLs must be valid http(s) pages."
    if parsed.scheme not in ("http", "https") or not host:
        return "Donor URLs must be http(s) pages."
    own = urlparse(public_base_url)
    if (host.rstrip(".").lower(), port) == ((own.hostname or "").lower(), own.port):
        return ""
    if _resolves_internal(host):
        return "Donor URLs must be public pages (internal addresses are not allowed)."
    return ""
