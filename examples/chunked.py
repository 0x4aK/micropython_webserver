"""
Exmple of sending chunked data.

You can send generated data by returning, or setting `resp.body`,
to an iterable of `bytes`
"""

import asyncio

# Importing `Request` and `Response` optional, used for typehinting
from uwebserver import Request, Response, WebServer

app = WebServer()


@app.route("/")
def hello(req: Request, resp: Response):
    def number_generator():
        for i in range(20):
            yield f"<h1>{i}</h1>".encode()

    resp.set_content_type("text/html")
    return number_generator()


asyncio.run(app.run())
