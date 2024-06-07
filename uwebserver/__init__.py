import asyncio
import gc
import os

try:
    import micropython
except ImportError:
    from types import SimpleNamespace

    micropython = SimpleNamespace(const=lambda i: i)

_is_coro = getattr(asyncio, "iscoroutinefunction", lambda f: type(f).__name__ == "generator")


try:
    from collections.abc import Awaitable, Callable, Iterable
    from typing import TYPE_CHECKING, Literal, TypeAlias, TypeGuard
except ImportError:
    TYPE_CHECKING = False


if TYPE_CHECKING:
    StrDict: TypeAlias = "dict[str,str]"
    BytesIter: TypeAlias = "Iterable[bytes]"
    Body: TypeAlias = "bytes|BytesIter|File|None"
    Results: TypeAlias = "Body|str"
    Handler: TypeAlias = "Callable[[Request,Response],Results|Awaitable[Results]]"
    ErrorHandler: TypeAlias = "Callable[[Request,Response,Exception],Results|Awaitable[Results]]"
    Methods: TypeAlias = "Iterable[Literal['GET','POST','DELETE','PUT','HEAD','OPTIONS']]"
    Encodings: TypeAlias = "Literal['gzip']"

_READ_SIZE = micropython.const(128)
_WRITE_BUFFER_SIZE = micropython.const(128)
_FILE_INDICATOR = micropython.const(1 << 16)
_INVALID_STATE = micropython.const(Exception("Invalid state"))

_WRITE_BUFFER = bytearray(_WRITE_BUFFER_SIZE)

MIME_TYPES = (
    ("css", "text/css"),
    ("png", "image/png"),
    ("html", "text/html"),
    ("jpg", "image/jpeg"),
    ("jpeg", "image/jpeg"),
    ("ico", "image/x-icon"),
    ("svg", "image/svg+xml"),
    ("json", "application/json"),
    ("js", "application/javascript"),
)


def get_mime(ext: str):
    for e, c in MIME_TYPES:
        if e == ext:
            return c


def _raise(e: BaseException):
    raise e


def _iterable(o) -> "TypeGuard[BytesIter]":
    return hasattr(o, "__next__") or hasattr(o, "__iter__")


async def _run(h, args: tuple) -> "Results":
    return await h(*args) if _is_coro(h) else h(*args)


def _split(b: str, sep: str, max: int | None = None):
    s = i = n = 0
    sl = len(sep)
    while (n < max if max is not None else True) and (i := b.find(sep, s)) > 0:
        yield b[s:i]
        s, n = i + sl, n + 1
    yield b[s:]


def _gc_after(f):
    async def w(*args):
        try:
            return await f(*args)
        except:
            raise
        finally:
            gc.collect()

    return w


def _parse_request(raw: str) -> tuple[str, str]:
    m, p, _ = r if len(r := tuple(_split(raw, " "))) == 3 else _raise(ValueError("Invalid request"))
    return m, p


def _parse_header(raw: str) -> tuple[str, str]:
    n, v = _split(raw, ":", 1)
    return n.lower(), v.strip()


def _parse_headers(raw: str) -> "StrDict":
    return dict(map(_parse_header, _split(raw, "\r\n")))


def _parse_qsv(qkv: str) -> tuple[str, str]:
    k, v = _split(qkv, "=", 1)
    return k, v


def _parse_qs(raw: str) -> "StrDict":
    return dict(map(_parse_qsv, _split(raw, "&")))


def _parse_path(raw: str) -> "tuple[str, StrDict | None]":
    p, rqs = _split(raw, "?", 1) if "?" in raw else (raw, None)
    return p, _parse_qs(rqs) if rqs is not None else None


def _get_file_size(path: str) -> int | None:
    try:
        s = os.stat(path)
    except OSError:
        return None
    if s[0] & _FILE_INDICATOR:
        return None
    return s[6]


class _Future:
    _o = object()

    def __init__(self) -> None:
        self._e = asyncio.Event()
        self._r = self._o
        self._er = None

    def set_result(self, result):
        self._r = result if self._r is self._o else _raise(_INVALID_STATE)
        self._e.set()

    def set_exception(self, exception: BaseException):
        self._er = exception if self._r is self._o else _raise(_INVALID_STATE)
        self._e.set()

    def result(self):
        if self._er is not None:
            raise self._er
        if self._r is self._o:
            raise _INVALID_STATE
        return self._r

    def __await__(self):
        yield from self._e.wait().__await__()
        return self.result()

    def __iter__(self):
        try:
            yield getattr(self._e.wait(), "__next__")()
        finally:
            return self.result()


class _Reader:
    def __init__(self, stream: asyncio.StreamReader):
        self.b, self.s = b"", stream

    async def readuntil(self, sep: bytes):
        b, sr = self.b, self.s.read
        while (i := b.find(sep)) < 0 and (d := await sr(_READ_SIZE)):
            b += d
            gc.collect()

        r, self.b = b[:i], b[i + len(sep) :]
        return r.decode()

    async def readexactly(self, n: int):
        b, sr = self.b, self.s.read
        while len(b) < n and (d := await sr(_READ_SIZE)):
            b += d
            gc.collect()

        r, self.b = b[:n], b[n:]
        return r.decode()


class File:
    def __init__(
        self,
        path: str,
        size: int | None = None,
        encoding: "Encodings | None" = None,
    ) -> None:
        self.size = size or _get_file_size(path) or _raise(OSError("Invalid file"))
        self.encoding = encoding
        self.path = path

    @classmethod
    def from_path(cls, path: str, encoding: "Encodings | None" = None):
        if s := _get_file_size(path):
            return cls(path, s, encoding)


class Request:
    def __init__(
        self,
        method: str,
        path: str,
        headers: "StrDict",
        query: "StrDict | None",
        body: str | None,
    ) -> None:
        self.method = method
        self.path = path
        self.headers = headers
        self.query = query
        self.body = body

    @classmethod
    @_gc_after
    async def from_stream(cls, s: asyncio.StreamReader) -> "Request":
        r = _Reader(s)
        m, rp = _parse_request(await r.readuntil(b"\r\n"))
        p, q = _parse_path(rp)
        h = _parse_headers(await r.readuntil(b"\r\n\r\n"))
        b = await r.readexactly(int(bl)) if (bl := h.get("content-length")) else None
        return cls(m, p, h, q, b)


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


class WebServer:
    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 80,
        static_folder: str | None = "static",
        request_timeout: float = 5,
    ) -> None:
        self.static = static_folder
        self.timeout = request_timeout
        self._h = host
        self._p = port
        self._r: "dict[tuple[str, str], Handler]" = {}
        self._cah: "Handler" = self._dch  # Catch-all handler
        self._eh: "ErrorHandler" = self._deh  # Error handler
        self._s: asyncio.Server | None = None
        self._re = _Future()

    def route(self, path: str, methods: "Methods" = ("GET",)):
        def w(handler: "Handler"):
            self.add_route(path, handler, methods)
            return handler

        return w

    def add_route(self, path: str, handler: "Handler", methods: "Methods" = ("GET",)):
        for method in methods:
            self._r[(method.upper(), path)] = handler

    @staticmethod
    def _dch(req: Request, resp: Response):
        "Default catch-all handler"
        resp.set_status(b"404 Not Found")
        return "Not Found"

    def catchall(self, h: "Handler"):
        self._cah = h
        return h

    @staticmethod
    def _deh(req: Request, resp: Response, e: Exception):
        "Default error handler"
        print("Error while handling:", repr(e))
        resp.set_status(b"500 Internal Server Error")
        resp.set_content_type("text/plain")
        return f"Error: {str(e)}"

    def error_handler(self, h: "ErrorHandler"):
        self._eh = h
        return h

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

    async def _respond_file(self, w, s: bytes, h: "StrDict", fi: File):
        h["content-length"] = str(fi.size)

        if fi.encoding is not None:
            h["content-encoding"] = fi.encoding

        if ct := get_mime((fi.path.rsplit(".", 2))[-2 if fi.encoding else -1]):
            h["content-type"] = ct

        await self._write_status(w, s)
        await self._write_headers(w, h)

        wb, ww, wd = memoryview(_WRITE_BUFFER), w.write, w.drain
        with open(fi.path, "rb") as f:
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

    def _get_static(self, req: Request):
        if self.static is None:
            return

        p = "./{}{}{}".format(
            self.static, req.path, ("index.html" if req.path.endswith("/") else "")
        )

        if (
            "gzip" in req.headers.get("accept-encoding", "")
            and (fi := File.from_path(p + ".gz", "gzip"))
            or (fi := File.from_path(p))
        ):
            return fi

    async def _handle_request(self, w, req: Request, resp: Response):
        try:
            if h := self._r.get((req.method, req.path)):
                r = await _run(h, (req, resp))
            elif req.method == "GET" and (fi := self._get_static(req)):
                r = fi
            else:
                r = await _run(self._cah, (req, resp))

        except Exception as e:
            r = await _run(self._eh, (req, resp, e))

        if r is not None:
            resp.set_body(r)

        if isinstance(resp.body, bytes) or resp.body is None:
            await self._respond(w, resp.status, resp.headers, resp.body)
        elif isinstance(resp.body, File):
            await self._respond_file(w, resp.status, resp.headers, resp.body)
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
        except MemoryError:
            await self._respond(w, b"413 Payload Too Large", resp.headers, b"Request too large")
        except Exception:
            await self._respond(w, b"400 Bad Request", resp.headers, b"Bad Request")
        else:
            await self._handle_request(w, req, resp)
        finally:
            w.close()
            await w.wait_closed()

    async def start(self):
        try:
            self._s = await asyncio.start_server(self._handle, self._h, self._p)
        except Exception as e:
            self._re.set_exception(e)
        else:
            self._re.set_result(None)

    def close(self):
        return self._s and self._s.close()

    async def wait_ready(self):
        await self._re

    async def wait_closed(self):
        return self._s and await self._s.wait_closed()

    async def run(self):
        await self.start()
        await self.wait_ready()
        await self.wait_closed()
