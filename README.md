# uWebServer

Simple, fast and memory efficient web server for MicroPython.

## ✅ Features

- Compatible with CPython.
- Type hints.
- Static files with GZip compression.

## ✏️ Examples

```python
import asyncio

# Importing `Request` and `Response` optional, used for typehinting
from uwebserver import Request, Response, WebServer

app = WebServer()


@app.route("/")
async def hello(req: Request, resp: Response):
    return "Hello world!"


asyncio.run(app.run())
```

**Check out [📁examples](https://github.com/0x4aK/uWebServer/blob/master/examples) for more**
