"""
Example of redirecting all unmatched routes
to the index page.
"""

import asyncio

# Importing Request and Response optional, used for typehinting
from uwebserver import Request, Response, WebServer

app = WebServer()


@app.route("/")
async def hello(req: Request, resp: Response):
    return "Hello world!"


@app.catchall
async def redirect_index(req: Request, resp: Response):
    resp.set_status("303 See Other")
    resp.set_header("location", "/")
    return "Redirected"


asyncio.run(app.run())
