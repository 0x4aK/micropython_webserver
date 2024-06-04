import asyncio
import unittest
from collections import namedtuple

import uwebserver

HOST, PORT = "127.0.0.1", 8000
TEST_TIMEOUT = 10


class Response(namedtuple("Response", "status headers body")):
    @classmethod
    def from_bytes(cls, raw: bytes):
        status, _, raw = raw.partition(b"\r\n")
        headers_raw, _, body = raw.partition(b"\r\n\r\n")
        return cls(status, uwebserver._parse_headers(headers_raw.decode()), body)


class Connection:
    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.writer = self.reader = None

    async def __aenter__(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        return self.reader, self.writer

    async def __aexit__(self, *args):
        if not self.writer:
            return
        self.writer.close()
        await self.writer.wait_closed()


async def _read_response(reader: asyncio.StreamReader):
    buffer = b""
    try:
        while data := await reader.read(512):
            buffer += data
    finally:
        return Response.from_bytes(buffer)


async def _write_data(
    writer: asyncio.StreamWriter,
    method: str,
    path: str,
    body: bytes | None,
):
    writer.write(b"%s %s HTTP/1.1\r\n" % tuple(map(str.encode, (method, path))))
    writer.write(b"Host: localhost\r\nConnection: keep-alive\r\nAccept-Encoding: gzip, deflate\r\n")

    if body:
        writer.write(b"Content-Length: %i\r\n\r\n" % len(body))
        writer.write(body)
    else:
        writer.write(b"\r\n")

    await writer.drain()


async def fetch(method: str, path: str, body: bytes | None):
    async with Connection(HOST, PORT) as (reader, writer):
        await _write_data(writer, method, path, body)
        return await _read_response(reader)


async def timeout(coro, timeout: int = TEST_TIMEOUT):
    return await asyncio.wait_for(coro, timeout)


class TestDefaultWebServer(unittest.TestCase):
    def setUp(self) -> None:
        def error_route(req, resp):
            raise Exception("Test Exception")

        # Default app with more suitable static folder and
        # request_timeout modified to speedup tests.
        self.static_folder = "examples/static"
        self.app = uwebserver.WebServer(
            port=PORT, static_folder=self.static_folder, request_timeout=1
        )
        # Mimic decorator behaviour
        self.app.route("/error")(error_route)

        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(timeout(self.app.start()))
        self.loop.run_until_complete(timeout(self.app.wait_ready()))

    def tearDown(self) -> None:
        self.app.close()
        self.loop.run_until_complete(timeout(self.app.wait_closed()))
        self.loop.close()

    def test_default_catchall_handler(self):
        expected = Response(
            b"HTTP/1.1 404 Not Found",
            {"connection": "close", "content-type": "text/plain", "content-length": "9"},
            b"Not Found",
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/invalid-route", None)))

        self.assertEqual(response, expected)

    def test_default_error_handler(self):
        expected = Response(
            b"HTTP/1.1 500 Internal Server Error",
            {"connection": "close", "content-type": "text/plain", "content-length": "21"},
            b"Error: Test Exception",
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/error", None)))

        self.assertEqual(response, expected)

    def test_static_file_handling(self):
        response = self.loop.run_until_complete(timeout(fetch("GET", "/", None)))
        with open("./" + self.static_folder + "/index.html", "rb") as f:
            expected = f.read()

        self.assertEqual(len(response.body), len(expected))
        self.assertEqual(response.headers.get("content-type"), "text/html")
        self.assertEqual(response.body, expected)

    def test_compressed_static_file_handling(self):
        response = self.loop.run_until_complete(timeout(fetch("GET", "/favicon.ico", None)))
        with open("./" + self.static_folder + "/favicon.ico.gz", "rb") as f:
            expected = f.read()

        self.assertEqual(len(response.body), len(expected))
        self.assertEqual(response.headers.get("content-encoding"), "gzip")
        self.assertEqual(response.body, expected)

    def test_invalid_request(self):
        async def send_invalid_request():
            async with Connection(HOST, PORT) as (reader, writer):
                writer.write(b"INVALID REQUEST\r\n")
                await writer.drain()
                return await _read_response(reader)

        expected = Response(
            b"HTTP/1.1 400 Bad Request",
            {"connection": "close", "content-type": "text/plain", "content-length": "11"},
            b"Bad Request",
        )

        response = self.loop.run_until_complete(timeout(send_invalid_request()))

        self.assertEqual(response, expected)

    def test_request_timeout(self):
        async def send_incomplete_request():
            async with Connection(HOST, PORT) as (reader, writer):
                writer.write(b"GET")
                await writer.drain()
                return await _read_response(reader)

        expected = Response(
            b"HTTP/1.1 408 Request timeout",
            {"connection": "close", "content-type": "text/plain", "content-length": "7"},
            b"Timeout",
        )

        response = self.loop.run_until_complete(timeout(send_incomplete_request()))

        self.assertEqual(response, expected)


class TestWebServer(unittest.TestCase):
    def setUp(self) -> None:
        def simple_route(req, resp):
            return "Hello"

        def iter_route(req, resp):
            return map(str.encode, "bytes iter test".split())

        def error_route(req, resp):
            return str(1 / 0)

        def echo_route(req, resp):
            return req.body.upper() if req.body else ""

        def no_content_route(req, resp):
            resp.set_status("204 No Content")

        async def async_route(req, resp):
            await asyncio.sleep(0)
            return "Asynchronous"

        def catchall_handler(req, resp):
            return "Catch-All"

        def error_handler(req, resp, error):
            resp.set_status(b"500 Internal Server Error")
            return "Error"

        self.app = uwebserver.WebServer(port=PORT)
        self.app.add_route("/simple", simple_route)
        self.app.add_route("/chunk", iter_route)
        self.app.add_route("/echo", echo_route, ("POST",))
        self.app.add_route("/error", error_route)
        self.app.add_route("/nothing", no_content_route)
        self.app.add_route("/async", async_route)
        self.app.catchall(catchall_handler)
        self.app.error_handler(error_handler)

        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(timeout(self.app.start()))
        self.loop.run_until_complete(timeout(self.app.wait_ready()))

    def tearDown(self) -> None:
        self.app.close()
        self.loop.run_until_complete(timeout(self.app.wait_closed()))
        self.loop.close()

    def test_simple_route(self):
        expected = Response(
            b"HTTP/1.1 200 OK",
            {"connection": "close", "content-type": "text/plain", "content-length": "5"},
            b"Hello",
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/simple", None)))

        self.assertEqual(response, expected)

    def test_post_data(self):
        body = b"test string"
        response = self.loop.run_until_complete(timeout(fetch("POST", "/echo", body)))

        self.assertEqual(response.body, body.upper())

    def test_no_content(self):
        expected = Response(
            b"HTTP/1.1 204 No Content",
            {"connection": "close", "content-type": "text/plain"},
            (b""),
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/nothing", None)))

        self.assertEqual(response, expected)

    def test_chunked_route(self):
        expected = Response(
            b"HTTP/1.1 200 OK",
            {"connection": "close", "content-type": "text/plain", "transfer-encoding": "chunked"},
            (b"5\r\nbytes\r\n4\r\niter\r\n4\r\ntest\r\n0\r\n\r\n"),
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/chunk", None)))

        self.assertEqual(response, expected)

    def test_async_handler(self):
        expected = Response(
            b"HTTP/1.1 200 OK",
            {"connection": "close", "content-type": "text/plain", "content-length": "12"},
            b"Asynchronous",
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/async", None)))

        self.assertEqual(response, expected)

    def test_catchall_handler(self):
        expected = Response(
            b"HTTP/1.1 200 OK",
            {"connection": "close", "content-type": "text/plain", "content-length": "9"},
            b"Catch-All",
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/invalid-route", None)))

        self.assertEqual(response, expected)

    def test_error_handler(self):
        expected = Response(
            b"HTTP/1.1 500 Internal Server Error",
            {"connection": "close", "content-type": "text/plain", "content-length": "5"},
            b"Error",
        )

        response = self.loop.run_until_complete(timeout(fetch("GET", "/error", None)))

        self.assertEqual(response, expected)


if __name__ == "__main__":
    unittest.main()
