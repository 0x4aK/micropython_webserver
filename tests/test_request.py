import asyncio
import unittest

import uwebserver


class MockStream(asyncio.StreamReader):
    def __init__(self, buffer: bytes) -> None:
        self.buffer = buffer
        self.pos = 0

    async def read(self, n: int):
        cur, self.pos = self.pos, self.pos + n
        return self.buffer[cur : self.pos]


class TestRequest(unittest.TestCase):
    def test_from_stream(self):
        cases = (
            (
                (
                    b"GET / HTTP/1.1\r\n"
                    b"Host: developer.mozilla.org\r\n"
                    b"Accept-Language: en\r\n"
                    b"\r\n"
                ),
                {
                    "method": "GET",
                    "path": "/",
                    "version": "HTTP/1.1",
                    "headers": {"host": "developer.mozilla.org", "accept-language": "en"},
                    "query": None,
                    "body": None,
                },
                "From stream GET request",
            ),
            (
                (
                    b"POST /contact_form.php HTTP/1.1\r\n"
                    b"Host: developer.mozilla.org\r\n"
                    b"Content-Length: 64\r\n"
                    b"Content-Type: application/json\r\n"
                    b"\r\n"
                    b'{"test":123,"other":"test"}'
                ),
                {
                    "method": "POST",
                    "path": "/contact_form.php",
                    "version": "HTTP/1.1",
                    "headers": {
                        "host": "developer.mozilla.org",
                        "content-length": "64",
                        "content-type": "application/json",
                    },
                    "query": None,
                    "body": '{"test":123,"other":"test"}',
                },
                "From stream Post request with body",
            ),
            (
                (
                    b"GET /test/demo_form.php?name1=value1&name2=value2 HTTP/1.1\r\n"
                    b"Host: developer.mozilla.org\r\n"
                    b"Accept-Encoding: gzip, deflate\r\n"
                    b"Connection: keep-alive\r\n"
                    b"\r\n"
                ),
                {
                    "method": "GET",
                    "path": "/test/demo_form.php",
                    "version": "HTTP/1.1",
                    "headers": {
                        "host": "developer.mozilla.org",
                        "accept-encoding": "gzip, deflate",
                        "connection": "keep-alive",
                    },
                    "query": {"name1": "value1", "name2": "value2"},
                    "body": None,
                },
                "From stream Post request with query string",
            ),
        )

        for buffer, expected_dict, msg in cases:
            with self.subTest(msg):
                req = asyncio.run(uwebserver.Request.from_stream(MockStream(buffer)))
                for attr, value in expected_dict.items():
                    self.assertEqual(getattr(req, attr), value)


if __name__ == "__main__":
    unittest.main()
