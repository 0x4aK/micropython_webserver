import asyncio

# Importing `Request` and `Response` optional, used for typehinting
from uwebserver import File, Request, Response, WebServer

app = WebServer()


@app.route("/")
def hello(req: Request, resp: Response):
    def body_gen():
        yield (
            b"""<!DOCTYPE html>"""
            b"""<html lang="en">"""
            b"""<head>"""
            b"""    <meta charset="UTF-8" />"""
            b"""    <meta name="viewport" content="width=device-width, initial-scale=1.0" />"""
            b"""    <title>Test page</title>"""
            b"""</head>"""
            b"""<body>"""
            b"""<ul>"""
        )

        for i in range(1, 3):
            yield f"""<li><a href="/page{i}">To page {i} without .html</a></li>""".encode()

        yield b"""</ul></body></html>"""

    resp.set_content_type("text/html")
    return body_gen()


@app.catchall
def catchall(req: Request, resp: Response):
    if app.static and (file := File.from_path(app.static + req.path + ".html")):
        return file

    resp.set_status("404 Not Found")
    return "File not found"


asyncio.run(app.run())
