import gc
import os

import micropython
import uasyncio

READ_BUFFER_SIZE = micropython.const(1024)
WRITE_BUFFER_SIZE = micropython.const(128)

FILE_INDICATOR = micropython.const(0x8000)


def parse_request(unparsed: str) -> tuple[str, str, str]:
    return tuple(unparsed.split(" ", 2))


def parse_path(unparsed: str) -> tuple[str, dict[str, str]]:
    path, raw_qs = unparsed.split("?", 1) if "?" in unparsed else (unparsed, "")

    return (
        path if not (path.endswith("/")) else path + "index.html",
        dict(q.split("=", 1) if "=" in q else [q, ""] for q in raw_qs.split("&")),
    )


def parse_headers(unparsed: str) -> dict[str, str]:
    return dict(h.split(": ", 1) for h in unparsed.split("\r\n"))


def get_file_size(path: str) -> int | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None

    if stat[0] ^ FILE_INDICATOR != 0:
        return None

    return stat[6]


class Response:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {"Connection": "close"}
        self.content_type = "text/plain"
        self.body: str | None = None
        self.status = "200 OK"

    def add_header(self, header: str, value: str):
        self.headers[header] = value


class Request:
    def __init__(
        self,
        method: str,
        path: str,
        version: str,
        headers: dict[str, str],
        qs: dict[str, str],
        body: str,
    ) -> None:
        self.path = path
        self.method = method
        self.version = version
        self.headers = headers
        self.body = body
        self.qs = qs


async def default_catchall(req: Request, resp: Response):
    resp.status = "404 Not Found"
    return "Not Found"


class WebServer:
    def __init__(self) -> None:
        self.routes = {}
        self.static = "/static"
        self.catchall = default_catchall

    def route(self, path: str, methods: list[str] | None = None):
        def wrapper(handler):
            self.add_route(path, handler, methods)
            return handler

        return wrapper

    def add_route(self, path: str, handler, methods: list[str] | None = None):
        for method in methods if methods is not None else ["GET"]:
            self.routes[(method, path)] = handler

    @staticmethod
    async def _write_status(writer, resp: Response) -> None:
        writer.write(f"HTTP/1.1 {resp.status}\r\n")
        await writer.drain()

    @staticmethod
    async def _write_headers(writer, resp: Response) -> None:
        for header, value in resp.headers.items():
            writer.write(f"{header}: {value}\r\n")

        writer.write("\r\n")
        await writer.drain()

    @staticmethod
    async def _write_body(writer, resp: Response) -> None:
        writer.write(str(resp.body))
        await writer.drain()

    @staticmethod
    def _parse_request(unparsed: str) -> Request:
        req_line, unparsed = unparsed.split("\r\n", 1)
        method, raw_path, version = parse_request(req_line)

        del req_line
        gc.collect()

        path, qs = parse_path(raw_path)

        del raw_path
        gc.collect()

        req_headers, body = unparsed.split("\r\n\r\n", 1)
        headers = parse_headers(req_headers)

        del req_headers
        gc.collect()

        return Request(method, path, version, headers, qs, body)

    async def _respond(self, writer, resp: Response):
        await self._write_status(writer, resp)

        if resp.body is not None:
            resp.add_header("Content-Length", str(len(resp.body)))
            await self._write_headers(writer, resp)
            await self._write_body(writer, resp)

        else:
            await self._write_headers(writer, resp)

    async def _respond_file(self, writer, resp: Response, path: str):

        mime_types = {
            "css": "text/css",
            "html": "text/html",
            "json": "application/json",
            "js": "application/javascript",
        }

        if mime_type := mime_types.get(path.rsplit(".", 1)[-1]):
            resp.content_type = mime_type

        await self._write_status(writer, resp)
        await self._write_headers(writer, resp)

        write_buffer = bytearray(WRITE_BUFFER_SIZE)

        with open(path, "rb") as f:
            while f.readinto(write_buffer):
                writer.write(write_buffer)
                await writer.drain()

    async def _handle_request(self, writer, req: Request, resp: Response):
        try:
            if handler := self.routes.get((req.method, req.path)):
                ret = await handler(req, resp)
                resp.body = ret if ret is not None else resp.body
                await self._respond(writer, resp)

            elif fsize := get_file_size(self.static + req.path):
                resp.add_header("Content-Length", str(fsize))
                await self._respond_file(writer, resp, self.static + req.path)

            else:
                ret = await self.catchall(req, resp)
                resp.body = ret if ret is not None else resp.body
                await self._respond(writer, resp)

        except Exception as e:
            resp.status = "500 Internal Server Error"
            resp.body = f"{type(e).__name__}: {e}"
            await self._respond(writer, resp)

    async def _handle(self, reader, writer):
        resp = Response()

        try:
            raw: str = (await reader.read(READ_BUFFER_SIZE)).decode()
            req = self._parse_request(raw)
            del raw

        except Exception as e:
            resp.status = "400 Bad Request"
            resp.body = f"{type(e).__name__}: {str(e)}"
            await self._respond(writer, resp)

        else:
            await self._handle_request(writer, req, resp)

        finally:
            writer.close()
            reader.close()
            await uasyncio.gather(writer.wait_closed(), reader.wait_closed())

    async def run(self, host: str = "0.0.0.0", port: int = 80):
        # TODO: Make this better
        await uasyncio.start_server(self._handle, host, port)
