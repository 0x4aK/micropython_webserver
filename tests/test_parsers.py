import unittest

import uwebserver


class TestParsers(unittest.TestCase):
    def test_parse_request(self):
        cases = (
            (
                "POST / HTTP/1.1",
                ("POST", "/", "HTTP/1.1"),
                "Request POST",
            ),
            (
                "GET /background.png HTTP/1.0",
                ("GET", "/background.png", "HTTP/1.0"),
                "Request GET",
            ),
            (
                "HEAD /test.html?query=alibaba HTTP/1.1",
                ("HEAD", "/test.html?query=alibaba", "HTTP/1.1"),
                "Request HEAD",
            ),
            (
                "OPTIONS /anypage.html HTTP/1.0",
                ("OPTIONS", "/anypage.html", "HTTP/1.0"),
                "Request OPTIONS",
            ),
        )

        for case, result, msg in cases:
            with self.subTest(msg):
                self.assertEqual(uwebserver._parse_request(case), result)

    def test_invalid_request(self):
        with self.assertRaises(ValueError):
            uwebserver._parse_request("INVALID REQUEST")

    def test_parse_headers(self):
        case = (
            "User-Agent: Mozilla/4.0 (compatible; MSIE5.01; Windows NT)\r\n"
            "Host: www.github.com\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: length\r\n"
            "Accept-Language: en-us\r\n"
            "Accept-Encoding: gzip, deflate\r\n"
            "Connection: Keep-Alive"
        )
        result = {
            "user-agent": "Mozilla/4.0 (compatible; MSIE5.01; Windows NT)",
            "host": "www.github.com",
            "content-type": "application/x-www-form-urlencoded",
            "content-length": "length",
            "accept-language": "en-us",
            "accept-encoding": "gzip, deflate",
            "connection": "Keep-Alive",
        }

        self.assertEqual(uwebserver._parse_headers(case), result)

    def test_parse_path(self):
        cases = (
            (
                "/test",
                ("/test", None),
                "Path plain",
            ),
            (
                "/anypage.html",
                ("/anypage.html", None),
                "Path with file extension",
            ),
            (
                "/test?query=alibaba",
                ("/test", dict(query="alibaba")),
                "Path with query value",
            ),
            (
                "/path/to/page?name=ferret&color=purple&style=class",
                ("/path/to/page", dict(name="ferret", color="purple", style="class")),
                "Path with multiple query values",
            ),
        )

        for case, result, msg in cases:
            with self.subTest(msg):
                self.assertEqual(uwebserver._parse_path(case), result)


if __name__ == "__main__":
    unittest.main()
