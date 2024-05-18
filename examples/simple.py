"""
Simple example of sending text back to the client
"""

import asyncio

# Importing `Request` and `Response` optional, used for typehinting
from uwebserver import Request, Response, WebServer

app = WebServer()


@app.route("/")
async def hello(req: Request, resp: Response):
    return "Hello world!"


asyncio.run(app.run())
