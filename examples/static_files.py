"""
Example of serving static files.
uWebServer will serve static files by default.
The static folder can be specified when defining WebServer instance or
disabled completely by passing `None`  as the static folder.

Static files can be plain files or GZip compressed.
If the clients browser supports GZip encoding, that file will be sent.
"""

import asyncio

# Importing Request and Response optional, used for typehinting
from uwebserver import WebServer

app = WebServer()

asyncio.run(app.run())
