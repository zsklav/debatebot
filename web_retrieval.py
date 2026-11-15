"""Key-free search plus bounded public-HTTPS reads. No browser credentials used."""

from datetime import datetime, timezone
from html.parser import HTMLParser
import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from ingestion import stable_id


class WebUnavailable(RuntimeError):
    pass


class TavilySearch:
    def __init__(self, api_key):
        self.api_key = api_key

    def text(self, query, *, max_results, **kwargs):
        import httpx
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.post("https://api.tavily.com/search", headers={
                "Authorization": "Bearer " + self.api_key}, json={"query": query,
                "max_results": max_results, "search_depth": "basic", "include_answer": False,
                "include_raw_content": False})
            response.raise_for_status()
            return [{"title": row.get("title"), "href": row.get("url"), "body": row.get("content")}
                    for row in response.json().get("results", [])]


def public_target(url, resolver=socket.getaddrinfo):
    """Return canonical URL, hostname and a validated IP for a pinned connection."""
    if not isinstance(url, str) or len(url) > 2048 or "\\" in url or any(ord(c) < 33 for c in url):
        raise ValueError("Invalid URL")
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.port not in (None, 443)):
        raise ValueError("Only credential-free public HTTPS on port 443 is supported")
    host = parts.hostname.encode("idna").decode("ascii")
    if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local", ".internal")):
        raise ValueError("Nonpublic host")
    addresses = resolver(host, 443, type=socket.SOCK_STREAM)
    ips = [item[4][0] for item in addresses]
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("Nonpublic address")
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, "")), host, ips[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # DNS is not resolved again: connect to the IP that passed validation.
        sock = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


class VisibleText(HTMLParser):
    ignored = {"script", "style", "noscript", "svg", "iframe", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.pieces = []

    def handle_starttag(self, tag, attrs):
        if tag in self.ignored:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in self.ignored:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.pieces.append(data)


def fetch_public_page(url, *, timeout=10, max_bytes=1_000_000, resolver=socket.getaddrinfo,
                      connection_factory=PinnedHTTPS, clock=time.monotonic):
    deadline = clock() + timeout
    for _ in range(4):
        canonical, host, address = public_target(url, resolver)
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Page fetch deadline reached")
        connection = connection_factory(host, address, remaining)
        try:
            parts = urlsplit(canonical)
            path = parts.path + ("?" + parts.query if parts.query else "")
            connection.request("GET", path, headers={"User-Agent": "DebateBot-Research/0.1",
                                "Accept": "text/html,text/plain", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("Missing redirect location")
                url = urljoin(canonical, location)
                continue  # Revalidate host/IP before every redirect.
            if response.status != 200:
                raise ValueError("Source page unavailable")
            content_type = response.getheader("Content-Type", "").split(";")[0].strip().lower()
            if content_type not in {"text/html", "text/plain"}:
                raise ValueError("Source is not supported HTML/text")
            if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
                raise ValueError("Compressed responses unsupported")
            data = bytearray()
            while True:
                remaining = deadline - clock()
                if remaining <= 0:
                    raise TimeoutError("Page fetch deadline reached")
                # Bound every body read by the remaining deadline and size limit.
                if connection.sock:
                    connection.sock.settimeout(remaining)
                block = response.read1(min(16384, max_bytes + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > max_bytes:
                    raise ValueError("Source page too large")
            decoded = data.decode("utf-8", errors="replace")
            if content_type == "text/html":
                parser = VisibleText()
                parser.feed(decoded)
                decoded = " ".join(parser.pieces)
            text = " ".join(decoded.split())
            if len(text) < 80:
                raise ValueError("Insufficient readable page content")
            return canonical, text
        finally:
            connection.close()
    raise ValueError("Too many redirects")


class WebEvidence:
    source_mode = "web"

    def __init__(self, *, search_client=None, fetcher=fetch_public_page, resolver=socket.getaddrinfo):
        self.provider = "injected"
        if search_client is None:
            provider = os.environ.get("DEBATEBOT_WEB_PROVIDER", "auto")
            if provider not in {"auto", "tavily", "ddgs"}:
                raise WebUnavailable("Unknown web provider")
            key = os.environ.get("TAVILY_API_KEY", "").strip()
            if provider == "tavily" or (provider == "auto" and key):
                if not key:
                    raise WebUnavailable("Configure TAVILY_API_KEY locally")
                search_client = TavilySearch(key)
                self.provider = "tavily"
            else:
                try:
                    from ddgs import DDGS
                except ImportError as error:
                    raise WebUnavailable("Install requirements-web.txt") from error
                search_client = DDGS(timeout=8, verify=True)
                self.provider = "ddgs-bing-best-effort"
        self.search_client, self.fetcher, self.resolver = search_client, fetcher, resolver
        self.fetched = {}

    def search(self, query, *, limit=2):
        if not query.strip() or len(query) > 500 or not 1 <= limit <= 2:
            raise ValueError("Invalid web query")
        try:
            rows = self.search_client.text(query, max_results=limit, backend="bing", safesearch="moderate")
        except Exception as error:
            raise WebUnavailable("Web search provider unavailable") from error
        hits, seen = [], set()
        for row in rows or []:
            try:
                url, _, _ = public_target(row.get("href", ""), self.resolver)
                if urlsplit(url).path.lower().endswith((".pdf", ".doc", ".docx", ".zip")):
                    continue  # This reader supports HTML/text only, not documents.
            except (ValueError, OSError):
                continue
            if url in seen:
                continue
            seen.add(url)
            hits.append({"chunk_id": stable_id("web-url-v1", url), "source_url": url,
                         "title": str(row.get("title", "Public source"))[:300],
                         "text": str(row.get("body", ""))[:700], "score": None,
                         "representation": "Search preview only; not fetched source evidence"})
            if len(hits) == limit:
                break
        return hits

    def read_passage(self, candidate, claim):
        url = candidate["source_url"]
        if url not in self.fetched:
            try:
                self.fetched[url] = self.fetcher(url)
            except Exception as error:
                raise WebUnavailable("Public source could not be read") from error
        final_url, text = self.fetched[url]
        # Inspect a short relevant excerpt, not an entire fetched page. Lexical
        # selection is a baseline, not semantic reranking or a truth classifier.
        words = list(re.finditer(r"\S+", text))
        terms = set(re.findall(r"[a-z]{4,}", claim.lower()))
        spans = [(words[i].start(), words[min(i + 180, len(words)) - 1].end())
                 for i in range(0, len(words), 150)]
        start, end = max(spans, key=lambda span: len(terms.intersection(
            re.findall(r"[a-z]{4,}", text[span[0]:span[1]].lower()))))
        end = min(end, start + 5500)
        return {**candidate, "source_url": final_url, "text": text[start:end],
                "content_hash": stable_id("web-text-v1", text), "source_id": final_url,
                "excerpt_start_char": start, "excerpt_end_char": end,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "representation": "Excerpt of normalized text fetched from a public HTTPS page; not a search snippet"}
