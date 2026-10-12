import os
import sys
import traceback

# Ensure both current directory and parent directory are on sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
for p in [current_dir, root_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ["VERCEL"] = "1"

try:
    from server import app as fastapi_app
    _startup_error = None
except Exception:
    fastapi_app = None
    _startup_error = traceback.format_exc()

async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    if scope["type"] == "http":
        if _startup_error:
            body = (
                f"<html><head><title>Startup Error</title></head><body style='font-family:sans-serif;padding:20px;background:#0f172a;color:#f8fafc;'>"
                f"<h2>Serverless Function Startup Error</h2>"
                f"<pre style='background:#1e293b;color:#f87171;padding:15px;border-radius:8px;overflow-x:auto;'>{_startup_error}</pre>"
                f"<p>sys.path: {sys.path}</p>"
                f"</body></html>"
            ).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii"))
                ]
            })
            await send({"type": "http.response.body", "body": body})
            return

        try:
            if scope.get("root_path") == "/":
                scope["root_path"] = ""
            await fastapi_app(scope, receive, send)
        except Exception:
            err_trace = traceback.format_exc()
            body = (
                f"<html><head><title>Runtime Error</title></head><body style='font-family:sans-serif;padding:20px;background:#0f172a;color:#f8fafc;'>"
                f"<h2>Runtime Error</h2>"
                f"<pre style='background:#1e293b;color:#f87171;padding:15px;border-radius:8px;overflow-x:auto;'>{err_trace}</pre>"
                f"</body></html>"
            ).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii"))
                ]
            })
            await send({"type": "http.response.body", "body": body})
