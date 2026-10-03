import os
import sys

# Ensure root directory is in sys.path for Vercel Serverless Function
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from server import app as fastapi_app

async def app(scope, receive, send):
    if scope.get("type") == "http":
        headers = dict(scope.get("headers", []))
        forwarded_uri = headers.get(b"x-forwarded-uri", b"").decode("utf-8")
        if not forwarded_uri:
            forwarded_uri = headers.get(b"x-url", b"").decode("utf-8")
            
        if forwarded_uri:
            clean_path = forwarded_uri.split("?")[0]
            if clean_path in ["/api/index.py", "/api/index"]:
                scope["path"] = "/"
            else:
                scope["path"] = clean_path
        else:
            path = scope.get("path", "")
            if path in ["/api/index.py", "/api/index", "/api", "/api/"]:
                scope["path"] = "/"
                
    await fastapi_app(scope, receive, send)
