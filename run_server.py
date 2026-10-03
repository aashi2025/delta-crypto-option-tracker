import os
import sys
import socket
import uvicorn
from dotenv import load_dotenv

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0").strip()
PORT = int(os.getenv("PORT", 8001))

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def print_startup_banner():
    local_ip = get_local_ip()
    print("\n" + "=" * 76)
    print("🚀 ASHISH GOSWAMI LTP CALCULATOR PRO - CRYPTO (DELTA EXCHANGE) SERVER")
    print("=" * 76)
    print("📌 Active Crypto Asset Support: BTC (Bitcoin), ETH (Ethereum), SOL (Solana)")
    print("📌 COA 1.0 & COA 2.0 Scenario Analysis Engine for Crypto Options")
    print("📌 Real-time Delta Exchange REST API Feed Integration")
    print("-" * 76)
    print("🌐 LOCAL COMPUTER ACCESS:")
    print(f"👉 http://localhost:{PORT}")
    print(f"👉 http://127.0.0.1:{PORT}")
    print("-" * 76)
    print("📱 MOBILE BROWSER (SAME WIFI / HOTSPOT) ACCESS:")
    print(f"👉 http://{local_ip}:{PORT}")
    print("=" * 76 + "\n")

if __name__ == "__main__":
    print_startup_banner()
    uvicorn.run("server:app", host=HOST, port=PORT, reload=True)
