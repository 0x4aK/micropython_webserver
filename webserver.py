import asyncio
import gc
import os
from collections import namedtuple

try:
    import micropython  # type: ignore
except ImportError:
    from types import SimpleNamespace

    micropython = SimpleNamespace(const=lambda i: i)


_READ_SIZE = micropython.const(256)
_WRITE_BUFFER_SIZE = micropython.const(128)
_FILE_INDICATOR = micropython.const(1 << 16)


def _raise(e: Exception):
    raise e


def _parse_request(raw: str) -> tuple[str, str, str]:
    r = raw.split(" ", 2)
    m, p, v = r if len(r) == 3 else _raise(ValueError("Invalid request line"))
    return m, p, v


def _parse_header(header: str) -> tuple[str, str]:
    n, _, v = header.partition(": ")
    return n.lower(), v


def _parse_headers(raw: str) -> dict[str, str]:
    return dict(map(_parse_header, raw.split("\r\n")))


def _parse_qsv(qkv: str) -> tuple[str, str]:
    k, _, v = qkv.partition("=")
    return k, v


def _parse_qs(raw: str) -> dict[str, str]:
    return dict(map(_parse_qsv, raw.split("&")))


def _parse_path(raw: str) -> tuple[str, dict[str, str] | None]:
    p, rqs = raw.split("?", 1) if "?" in raw else (raw, None)
    return p, _parse_qs(rqs) if rqs is not None else None


def _get_file_size(path: str) -> int | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if stat[0] & _FILE_INDICATOR != 0:
        return None
    return stat[6]


_FileInfo = namedtuple("_FileInfo", "path,size,encoding")


class _Reader:
    def __init__(self, stream: asyncio.StreamReader):
        self.b = b""
        self.s = stream

    async def readuntil(self, sep=b"\n"):
        while (i := self.b.find(sep)) < 0 and (d := await self.s.read(_READ_SIZE)):
            self.b += d

        r, self.b = self.b[:i], self.b[i + len(sep) :]
        return r.decode()

    async def readexactly(self, n: int):
        while len(self.b) < n and (d := await self.s.read(_READ_SIZE)):
            self.b += d

        r, self.b = self.b[:n], self.b[n:]
        return r.decode()


class _Body:
    def __set__(self, i, v: str | None):
        setattr(i, "_body", v and v.encode())

    def __get__(self, i, objtype=None) -> bytes | None:
        return getattr(i, "_body", None)


class Response:
    body = _Body()

    def __init__(self):
        self.headers: dict[str, str] = {"connection": "close"}
        self.content_type = "text/plain"
        self.body = None
        self.status = "200 OK"

    def set_header(self, header: str, value: str):
        self.headers[header] = value

    def set_status(self, status: str):
        self.status = status

    def set_body(self, body: str):
        self.body = body

    def set_content_type(self, ct: str):
        self.content_type = ct


class Request:
    def __init__(
        self,
        method: str,
        path: str,
        version: str,
        headers: dict[str, str],
        qs: dict[str, str] | None,
        body: str | None,
    ) -> None:
        self.path = path
        self.method = method
        self.version = version
        self.headers = headers
        self.body = body
        self.qs = qs

    @classmethod
    async def from_stream(cls, stream: asyncio.StreamReader) -> "Request":
        r = _Reader(stream)

        m, rp, v = _parse_request(await r.readuntil(b"\r\n"))
        p, qs = _parse_path(rp)
        h = _parse_headers(await r.readuntil(b"\r\n\r\n"))
        b = await r.readexactly(int(bl)) if (bl := h.get("content-length")) else None
        return cls(m, p, v, h, qs, b)


class WebServer:
    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 80,
        static_folder: str = "static",
    ) -> None:
        self.host = host
        self.port = port
        self.routes = {}
        self.static = static_folder
        self._cah = self._dch  # Catch-all handler
        self._eh = self._deh  # Error handler
        self.s: asyncio.Server | None = None

    def route(self, path: str, methods: tuple[str, ...] = ("GET",)):
        def w(handler):
            self.add_route(path, handler, methods)
            return handler

        return w

    def add_route(self, path: str, handler, methods: tuple[str, ...] = ("GET",)):
        for method in methods:
            self.routes[(method.upper(), path)] = handler

    @staticmethod
    async def _dch(req: Request, resp: Response):
        "Default catch-all handler"
        resp.status = "404 Not Found"
        return "Not Found"

    def catchall(self, handler):
        self.set_catchall(handler)
        return handler

    def set_catchall(self, handler):
        self._cah = handler

    @staticmethod
    async def _deh(req: Request, resp: Response, error: BaseException):
        "Default error handler"
        resp.status = "500 Internal Server Error"
        return f"Error: {str(error)}"

    def error_handler(self, handler):
        self.set_error_handler(handler)
        return handler

    def set_error_handler(self, handler):
        self._eh = handler

    @staticmethod
    async def _write_status(w, resp: Response):
        w.write(f"HTTP/1.1 {resp.status}\r\n".encode())
        await w.drain()

    @staticmethod
    async def _write_headers(w, resp: Response):
        w.write(f"content-type: {resp.content_type}\r\n".encode())
        await w.drain()

        for header, value in resp.headers.items():
            w.write(f"{header}: {value}\r\n".encode())
            await w.drain()

        w.write(b"\r\n")
        await w.drain()

    @staticmethod
    async def _write_body(w, resp: Response):
        w.write(resp.body)
        await w.drain()

    async def _respond(self, w, resp: Response):
        await self._write_status(w, resp)

        if resp.body is not None:
            resp.set_header("content-length", str(len(resp.body)))
            await self._write_headers(w, resp)
            await self._write_body(w, resp)

        else:
            await self._write_headers(w, resp)

    async def _respond_file(self, w, resp: Response, path: str):
        mts = {
            "css": "text/css",
            "png": "image/png",
            "html": "text/html",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "ico": "image/x-icon",
            "svg": "image/svg+xml",
            "json": "application/json",
            "js": "application/javascript",
        }

        exts = path.rsplit(".", 2)
        if mt := mts.get(exts[-2] if exts[-1] == "gz" else exts[-1]):
            resp.content_type = mt

        await self._write_status(w, resp)
        await self._write_headers(w, resp)

        wb = bytearray(_WRITE_BUFFER_SIZE)
        with open(path, "rb") as f:
            while f.readinto(wb):
                w.write(wb)
                await w.drain()

    def _get_static(self, req: Request):
        path = "./" + self.static + req.path + ("index.html" if req.path.endswith("/") else "")

        if (
            (en := req.headers.get("accept-encoding"))
            and "gzip" in en
            and (fsize := _get_file_size(path + ".gz"))
        ):
            return _FileInfo(path + ".gz", fsize, "gzip")

        elif fsize := _get_file_size(path):
            return _FileInfo(path, fsize, None)

    async def _handle_static(self, w, fi: _FileInfo, resp: Response):
        resp.set_header("content-length", str(fi.size))
        if fi.encoding:
            resp.set_header("content-encoding", fi.encoding)
        await self._respond_file(w, resp, fi.path)

    async def _handle_request(self, w, req: Request, resp: Response):
        try:
            if handler := self.routes.get((req.method, req.path)):
                results = await handler(req, resp)
            elif req.method == "GET" and (fi := self._get_static(req)):
                await self._handle_static(w, fi, resp)
                return
            else:
                results = await self._cah(req, resp)

        except Exception as e:
            print("Error while handling:", repr(e))
            results = await self._eh(req, resp, e)

        if results is not None:
            resp.body = results

        await self._respond(w, resp)

    async def _handle(self, r, w):
        print("Got request from", w.get_extra_info("peername"))
        resp = Response()

        try:
            req = await Request.from_stream(r)
            gc.collect()

        except Exception as e:
            print("Error while parsing:", repr(e))
            resp.status = "400 Bad Request"
            resp.body = "Bad Request"
            await self._respond(w, resp)

        else:
            await self._handle_request(w, req, resp)

        finally:
            w.close()

    def close(self):
        return self.s and self.s.close()

    async def run(self):
        self.s = await asyncio.start_server(self._handle, self.host, self.port)
        await self.s.wait_closed()
