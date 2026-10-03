import os
import sys

# Ensure root directory is in sys.path for Vercel Serverless Function
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from server import app as fastapi_app

async def app(scope, receive, send):
    if scope.get("type") == "http":
        path = scope.get("path", "")
        # Strip Vercel serverless script prefix
        if path in ["/api/index.py", "/api/index", "/api"]:
            scope["path"] = "/"
        elif path.startswith("/api/index.py/"):
            scope["path"] = path[len("/api/index.py"):]
        elif path.startswith("/api/index/"):
            scope["path"] = path[len("/api/index"):]
    await fastapi_app(scope, receive, send)
