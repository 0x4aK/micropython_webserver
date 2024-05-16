import asyncio
import unittest
from collections import namedtuple

import uwebserver

HOST, PORT = "127.0.0.1", 8000
APP_TIMEOUT, TEST_TIMEOUT = 30, 10


class Response(namedtuple("Response", "status headers body")):
    @classmethod
    def from_bytes(cls, raw: bytes):
        status, _, raw = raw.partition(b"\r\n")
        headers_raw, _, body = raw.partition(b"\r\n\r\n")
        return cls(status, uwebserver._parse_headers(headers_raw.decode()), body)


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
    writer.write(b"Host: localhost\r\nConnection: keep-alive\r\n")

    if body:
        writer.write(b"Content-Length: %s\r\n\r\n" % len(body))
        writer.write(body)
    else:
        writer.write(b"\r\n")

    await writer.drain()


async def fetch(method: str, path: str, body: bytes | None):
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
        await _write_data(writer, method, path, body)
        return await _read_response(reader)
    finally:
        writer.close()
        await writer.wait_closed()


class TestDefaultWebServer(unittest.TestCase):
    def setUp(self) -> None:
        async def error_route(req, resp):
            raise Exception("Test Exception")

        self.app = uwebserver.WebServer(host=HOST, port=PORT)
        self.app.add_route("/error", error_route)

        self.loop = asyncio.new_event_loop()
        self.app_task = self.loop.create_task(asyncio.wait_for(self.app.run(), APP_TIMEOUT))

    def tearDown(self) -> None:
        self.app.close()
        self.loop.run_until_complete(self.app_task)
        self.loop.close()

    def test_default_catchall_handler(self):
        expected = Response(
            b"HTTP/1.1 404 Not Found",
            {"connection": "close", "content-type": "text/plain", "content-length": "9"},
            b"Not Found",
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(fetch("GET", "/this/route/does/not/exist", None), TEST_TIMEOUT),
        )

        self.assertEqual(response, expected)

    def test_default_error_handler(self):
        expected = Response(
            b"HTTP/1.1 500 Internal Server Error",
            {"connection": "close", "content-type": "text/plain", "content-length": "21"},
            b"Error: Test Exception",
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(fetch("GET", "/error", None), TEST_TIMEOUT),
        )

        self.assertEqual(response, expected)

    def test_invalid_request(self):
        async def send_invalid_request():
            try:
                reader, writer = await asyncio.open_connection(HOST, PORT)
                writer.write(b"INVALID REQUEST\r\n")
                await writer.drain()
                return await _read_response(reader)
            finally:
                writer.close()
                await writer.wait_closed()

        expected = Response(
            b"HTTP/1.1 400 Bad Request",
            {"connection": "close", "content-type": "text/plain", "content-length": "11"},
            b"Bad Request",
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(send_invalid_request(), TEST_TIMEOUT),
        )

        self.assertEqual(response, expected)

    def test_request_timeout(self):
        async def send_incomplete_request():
            try:
                reader, writer = await asyncio.open_connection(HOST, PORT)
                writer.write(b"GET")
                await writer.drain()
                return await _read_response(reader)
            finally:
                writer.close()
                await writer.wait_closed()

        expected = Response(
            b"HTTP/1.1 408 Request timeout",
            {"connection": "close", "content-type": "text/plain", "content-length": "7"},
            b"Timeout",
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(send_incomplete_request(), TEST_TIMEOUT),
        )
        self.assertEqual(response, expected)


class TestWebServer(unittest.TestCase):
    def setUp(self) -> None:
        async def simple_route(req, resp):
            return "Hello"

        async def iter_route(req, resp):
            return map(str.encode, "bytes iter test".split())

        async def error_route(req, resp):
            return str(1 / 0)

        async def catchall_handler(req, resp):
            return "Catch-All"

        async def error_handler(req, resp, error):
            resp.set_status(b"500 Internal Server Error")
            return "Error"

        self.app = uwebserver.WebServer(host=HOST, port=PORT)
        self.app.add_route("/simple", simple_route)
        self.app.add_route("/chunk", iter_route)
        self.app.add_route("/error", error_route)
        self.app.catchall(catchall_handler)
        self.app.error_handler(error_handler)

        self.loop = asyncio.new_event_loop()
        self.app_task = self.loop.create_task(asyncio.wait_for(self.app.run(), APP_TIMEOUT))

    def tearDown(self) -> None:
        self.app.close()
        self.loop.run_until_complete(self.app_task)
        self.loop.close()

    def test_simple_route(self):
        expected = Response(
            b"HTTP/1.1 200 OK",
            {"connection": "close", "content-type": "text/plain", "content-length": "5"},
            b"Hello",
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(fetch("GET", "/simple", None), TEST_TIMEOUT)
        )

        self.assertEqual(response, expected)

    def test_chunked_route(self):
        expected = Response(
            b"HTTP/1.1 200 OK",
            {"connection": "close", "content-type": "text/plain", "transfer-encoding": "chunked"},
            (b"5\r\nbytes\r\n4\r\niter\r\n4\r\ntest\r\n0\r\n\r\n"),
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(fetch("GET", "/chunk", None), TEST_TIMEOUT)
        )

        self.assertEqual(response, expected)

    def test_catchall_handler(self):
        expected = Response(
            b"HTTP/1.1 200 OK",
            {
                "connection": "close",
                "content-type": "text/plain",
                "content-length": "9",
            },
            b"Catch-All",
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(fetch("GET", "/this/route/does/not/exist", None), TEST_TIMEOUT)
        )

        self.assertEqual(response, expected)

    def test_error_handler(self):
        expected = Response(
            b"HTTP/1.1 500 Internal Server Error",
            {
                "connection": "close",
                "content-type": "text/plain",
                "content-length": "5",
            },
            b"Error",
        )

        response = self.loop.run_until_complete(
            asyncio.wait_for(fetch("GET", "/error", None), TEST_TIMEOUT),
        )

        self.assertEqual(response, expected)


if __name__ == "__main__":
    unittest.main()
