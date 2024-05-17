import asyncio
import gc
import os
from collections import namedtuple

try:
    from collections.abc import Callable, Coroutine, Iterable
    from typing import TYPE_CHECKING, Literal, TypeAlias, TypeGuard
except ImportError:
    TYPE_CHECKING = False


if TYPE_CHECKING:
    BytesIter: TypeAlias = "Iterable[bytes]"
    Body: TypeAlias = "bytes|BytesIter|None"
    Results: TypeAlias = "Coroutine[None,None,Body|str]"
    Handler: TypeAlias = "Callable[[Request,Response],Results]"
    ErrorHanlder: TypeAlias = "Callable[[Request,Response,Exception],Results]"
    Methods: TypeAlias = "Iterable[Literal['GET','POST','DELETE','PUT','HEAD','OPTIONS']]"
    StrDict: TypeAlias = "dict[str,str]"

try:
    import micropython
except ImportError:
    from types import SimpleNamespace

    micropython = SimpleNamespace(const=lambda i: i)

_READ_SIZE = micropython.const(128)
_WRITE_BUFFER_SIZE = micropython.const(128)
_FILE_INDICATOR = micropython.const(1 << 16)
MIME_TYPES = {
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


_FileInfo = namedtuple("_FileInfo", "path size encoding")


def _iterable(o: object) -> "TypeGuard[BytesIter]":
    return hasattr(o, "__next__") or hasattr(o, "__iter__")


def _raise(e: Exception):
    raise e


def _gc_after(f):
    async def w(*args):
        try:
            return await f(*args)
        except:
            raise
        finally:
            gc.collect()

    return w


def _parse_request(raw: str) -> tuple[str, str, str]:
    m, p, v = r if len(r := raw.split(" ")) == 3 else _raise(ValueError("Invalid request line"))
    return m, p, v


def _parse_header(header: str) -> tuple[str, str]:
    n, _, v = header.partition(":")
    return n.lower(), v.strip()


def _parse_headers(raw: str) -> "StrDict":
    return dict(map(_parse_header, raw.split("\r\n")))


def _parse_qsv(qkv: str) -> tuple[str, str]:
    k, _, v = qkv.partition("=")
    return k, v


def _parse_qs(raw: str) -> "StrDict":
    return dict(map(_parse_qsv, raw.split("&")))


def _parse_path(raw: str) -> "tuple[str, StrDict | None]":
    p, rqs = raw.split("?", 1) if "?" in raw else (raw, None)
    return p, _parse_qs(rqs) if rqs is not None else None


def _get_file_size(path: str) -> int | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if stat[0] & _FILE_INDICATOR:
        return None
    return stat[6]


class _Reader:
    def __init__(self, stream: asyncio.StreamReader):
        self.b = b""
        self.s = stream

    async def readuntil(self, sep=b"\n"):
        b, sr = self.b, self.s.read
        while (i := b.find(sep)) < 0 and (d := await sr(_READ_SIZE)):
            b += d

        r, self.b = b[:i], b[i + len(sep) :]
        return r.decode()

    async def readexactly(self, n: int):
        b, sr = self.b, self.s.read
        while len(b) < n and (d := await sr(_READ_SIZE)):
            b += d

        r, self.b = b[:n], b[n:]
        return r.decode()


class Response:
    def __init__(self):
        self.headers = {"connection": "close", "content-type": "text/plain"}
        self.body: "Body" = None
        self.status = b"200 OK"

    def set_header(self, header: str, value: str):
        self.headers[header] = value

    def set_status(self, status: bytes | str):
        self.status = status.encode() if isinstance(status, str) else status

    def set_body(self, body: "Body | str"):
        self.body = body.encode() if isinstance(body, str) else body

    def set_content_type(self, ct: str):
        self.headers["content-type"] = ct


class Request:
    def __init__(
        self,
        method: str,
        path: str,
        version: str,
        headers: "StrDict",
        qs: "StrDict | None",
        body: str | None,
    ) -> None:
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers
        self.qs = qs
        self.body = body

    @classmethod
    @_gc_after
    async def from_stream(cls, s: asyncio.StreamReader) -> "Request":
        r = _Reader(s)

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
        static_folder: str | None = "static",
        request_timeout: float = 5,
    ) -> None:
        self.host = host
        self.port = port
        self.r: dict[tuple[str, str], Handler] = {}
        self.static = static_folder
        self.timeout = request_timeout
        self._cah: "Handler" = self._dch  # Catch-all handler
        self._eh: "ErrorHanlder" = self._deh  # Error handler
        self.s: asyncio.Server | None = None
        self._ready = asyncio.Event()

    def route(self, path: str, methods: "Methods" = ("GET",)):
        def w(handler: "Handler"):
            self.add_route(path, handler, methods)
            return handler

        return w

    def add_route(self, path: str, handler: "Handler", methods: "Methods" = ("GET",)):
        for method in methods:
            self.r[(method.upper(), path)] = handler

    @staticmethod
    async def _dch(req: Request, resp: Response):
        "Default catch-all handler"
        resp.set_status(b"404 Not Found")
        return "Not Found"

    def catchall(self, h: "Handler"):
        self.set_catchall(h)
        return h

    def set_catchall(self, h: "Handler"):
        self._cah = h

    @staticmethod
    async def _deh(req: Request, resp: Response, e: Exception):
        "Default error handler"
        print("Error while handling:", repr(e))
        resp.set_status(b"500 Internal Server Error")
        resp.set_content_type("text/plain")
        return f"Error: {str(e)}"

    def error_handler(self, h: "ErrorHanlder"):
        self.set_error_handler(h)
        return h

    def set_error_handler(self, h: "ErrorHanlder"):
        self._eh = h

    @staticmethod
    async def _write_status(w, s: bytes):
        w.write(b"HTTP/1.1 %s\r\n" % s)
        await w.drain()

    @staticmethod
    async def _write_headers(w, h: "StrDict"):
        for v in map(lambda v: tuple(map(str.encode, v)), h.items()):
            w.write(b"%s: %s\r\n" % v)
        w.write(b"\r\n")
        await w.drain()

    async def _respond(self, w, s: bytes, h: "StrDict", b: bytes | None):
        await self._write_status(w, s)

        if b is None:
            await self._write_headers(w, h)
            return

        h["content-length"] = str(len(b))
        await self._write_headers(w, h)
        w.write(b)
        await w.drain()

    async def _respond_file(self, w, s: bytes, h: "StrDict", p: str):
        ext = (e := p.rsplit(".", 2))[-2 if e[-1] == "gz" else -1]
        if ct := MIME_TYPES.get(ext):
            h["content-type"] = ct

        await self._write_status(w, s)
        await self._write_headers(w, h)

        wb, ww, wd = memoryview(bytearray(_WRITE_BUFFER_SIZE)), w.write, w.drain
        with open(p, "rb") as f:
            while r := f.readinto(wb):
                ww(wb[:r])
                await wd()

    async def _respond_chunks(self, w, s: bytes, h: "StrDict", i: "BytesIter"):
        h["transfer-encoding"] = "chunked"
        await self._write_status(w, s)
        await self._write_headers(w, h)

        ww, wd = w.write, w.drain
        for d in i:
            ww(b"%x\r\n%s\r\n" % (len(d), d))
            await wd()
        ww(b"0\r\n\r\n")
        await wd()

    def _get_static_info(self, req: Request):
        if self.static is None:
            return

        p = "./" + self.static + req.path + ("index.html" if req.path.endswith("/") else "")
        e = req.headers.get("accept-encoding", "")

        if "gzip" in e and (fsize := _get_file_size(p + ".gz")):
            return _FileInfo(p + ".gz", fsize, "gzip")

        elif fsize := _get_file_size(p):
            return _FileInfo(p, fsize, None)

    async def _handle_static(self, w, resp: Response, fi: _FileInfo):
        resp.set_header("content-length", str(fi.size))
        if fi.encoding:
            resp.set_header("content-encoding", fi.encoding)
        await self._respond_file(w, resp.status, resp.headers, fi.path)

    async def _handle_request(self, w, req: Request, resp: Response):
        try:
            if handler := self.r.get((req.method, req.path)):
                r = await handler(req, resp)
            elif req.method == "GET" and (fi := self._get_static_info(req)):
                await self._handle_static(w, resp, fi)
                return
            else:
                r = await self._cah(req, resp)

        except Exception as e:
            r = await self._eh(req, resp, e)

        if r is not None:
            resp.set_body(r)

        if isinstance(resp.body, bytes) or resp.body is None:
            await self._respond(w, resp.status, resp.headers, resp.body)
        elif _iterable(resp.body):
            await self._respond_chunks(w, resp.status, resp.headers, resp.body)
        else:
            await self._respond(w, resp.status, resp.headers, str(resp.body).encode())

    @_gc_after
    async def _handle(self, r, w):
        resp = Response()

        try:
            req = await asyncio.wait_for(Request.from_stream(r), self.timeout)
        except asyncio.TimeoutError:
            await self._respond(w, b"408 Request timeout", resp.headers, b"Timeout")
        except Exception:
            await self._respond(w, b"400 Bad Request", resp.headers, b"Bad Request")
        else:
            await self._handle_request(w, req, resp)
        finally:
            w.close()
            await w.wait_closed()

    def close(self):
        return self.s and self.s.close()

    async def wait_ready(self):
        await self._ready.wait()

    async def run(self):
        self.s = await asyncio.start_server(self._handle, self.host, self.port)
        self._ready.set()
        await self.s.wait_closed()
