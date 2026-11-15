"""Public URL boundaries and search/read behavior, without network access."""

from io import BytesIO
import socket
import unittest
from unittest.mock import Mock, patch

from web_retrieval import PinnedHTTPS, TavilySearch, WebEvidence, WebUnavailable, fetch_public_page, public_target


def resolver(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))]


class Response:
    def __init__(self, data=b"", status=200, headers=None):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {"Content-Type": "text/html"}).items()}
        self.body = BytesIO(data)
    def getheader(self, key, default=None):
        return self.headers.get(key.lower(), default)
    def read1(self, size):
        return self.body.read(size)


class WebTests(unittest.TestCase):
    def test_tavily_adapter_sends_key_only_in_authorization_and_maps_results(self):
        with patch("httpx.Client") as factory:
            http = factory.return_value.__enter__.return_value
            http.post.return_value.json.return_value = {"results":[{"title":"Study","url":"https://example.org/","content":"Preview"}]}
            rows = TavilySearch("test-provider-key").text("public query",max_results=2)
        request = http.post.call_args
        self.assertEqual(request.args[0],"https://api.tavily.com/search")
        self.assertEqual(request.kwargs["headers"]["Authorization"],"Bearer test-provider-key")
        self.assertNotIn("test-provider-key",str(request.kwargs["json"]))
        self.assertFalse(request.kwargs["json"]["include_raw_content"])
        self.assertEqual(rows[0]["href"],"https://example.org/")

    def test_nonpublic_schemes_ports_credentials_and_addresses_are_rejected(self):
        for url in ("http://example.org/", "file:///etc/passwd", "https://user:pass@example.org/",
                    "https://example.org:8443/", "https://localhost/", "https://service.local/",
                    "https://example.org/\nheader", "https://example.org\\x/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_target(url, resolver)
        for address in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "::ffff:192.168.1.2"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                public_target("https://example.org/", lambda *a, **k: [(2,1,6,"",(address,443))])

    def test_mixed_public_private_dns_is_rejected(self):
        mixed = lambda *a, **k: [(2,1,6,"",("1.1.1.1",443)), (2,1,6,"",("127.0.0.1",443))]
        with self.assertRaises(ValueError):
            public_target("https://example.org/", mixed)

    def test_connection_uses_validated_ip_with_original_tls_hostname(self):
        connection = PinnedHTTPS("example.org", "1.1.1.1", 5)
        connection._context = Mock()
        sock = Mock()
        with patch("socket.create_connection", return_value=sock) as create:
            connection.connect()
        create.assert_called_once_with(("1.1.1.1",443), timeout=5)
        connection._context.wrap_socket.assert_called_once_with(sock, server_hostname="example.org")
        connection.close()

    def factory(self, response):
        connection = Mock()
        connection.getresponse.return_value = response
        return Mock(return_value=connection), connection

    def test_fetch_strips_scripts_and_closes_connection(self):
        html = b"<html><script>execute_bad_code()</script><main><p>" + b"Public study findings and limitations. " * 5 + b"</p></main></html>"
        factory, connection = self.factory(Response(html))
        url, text = fetch_public_page("https://example.org/study", resolver=resolver, connection_factory=factory)
        self.assertNotIn("execute_bad_code", text)
        self.assertIn("Public study", text)
        self.assertEqual(url, "https://example.org/study")
        self.assertEqual(factory.call_args.args[:2], ("example.org", "1.1.1.1"))
        connection.close.assert_called_once()

    def test_redirect_to_private_target_is_revalidated_before_connecting(self):
        factory, connection = self.factory(Response(status=302, headers={"Location":"https://localhost/private"}))
        with self.assertRaises(ValueError):
            fetch_public_page("https://example.org/study", resolver=resolver, connection_factory=factory)
        self.assertEqual(factory.call_count,1)
        connection.close.assert_called_once()

    def test_oversized_compressed_and_nontext_responses_are_rejected(self):
        responses = [Response(b"x"*100), Response(b"x",headers={"Content-Type":"application/pdf"}),
                     Response(b"x",headers={"Content-Type":"text/html","Content-Encoding":"gzip"})]
        for response in responses:
            factory, connection = self.factory(response)
            with self.assertRaises(ValueError):
                fetch_public_page("https://example.org/", resolver=resolver, connection_factory=factory, max_bytes=50)
            connection.close.assert_called_once()

    def test_search_returns_only_candidates_and_reads_fetch_source_text(self):
        search = Mock()
        search.text.return_value = [{"title":"A study", "href":"https://example.org/study", "body":"Search snippet not evidence"}]
        fetcher = Mock(return_value=("https://example.org/study", "Filler text. "*200 + "CEO ability findings from the original study. "*30))
        web = WebEvidence(search_client=search, fetcher=fetcher, resolver=resolver)
        candidate = web.search("CEO ability")[0]
        self.assertIn("preview", candidate["representation"])
        read = web.read_passage(candidate,"CEO ability")
        self.assertNotIn("Search snippet",read["text"])
        self.assertLessEqual(len(read["text"].split()),180)
        self.assertIn("retrieved_at",read)
        self.assertIn("fetched",read["representation"])
        web.read_passage(candidate,"CEO ability")
        fetcher.assert_called_once()

    def test_provider_failure_is_an_error_not_zero_evidence(self):
        search = Mock()
        search.text.side_effect = RuntimeError("private provider details")
        web = WebEvidence(search_client=search,resolver=resolver)
        with self.assertRaisesRegex(WebUnavailable,"provider unavailable"):
            web.search("CEO ability")

    def test_private_pdf_and_duplicate_search_results_are_filtered(self):
        search = Mock()
        search.text.return_value = [
            {"href":"https://localhost/private"}, {"href":"https://example.org/study.pdf"},
            {"href":"https://example.org/study"}, {"href":"https://example.org/study#section"}]
        web = WebEvidence(search_client=search,resolver=resolver)
        self.assertEqual(len(web.search("CEO ability")),1)
