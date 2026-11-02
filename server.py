import os
import sys
import io
from datetime import datetime
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from gtts import gTTS

from delta_service import DeltaExchangeService
from database import OptionChainDB
from calculation_engine import OptionChainCalculationEngine
from alert_engine import OptionChainAlertEngine
from trade_audit_engine import TradeAuditEngine, IST, get_ist_str, get_ist_now
from whatsapp_alert_engine import WhatsAppAlertService
from telegram_alert_engine import TelegramAlertService
from pydantic import BaseModel

# Load environment variables
load_dotenv()

DELTA_API_KEY = os.getenv("DELTA_API_KEY", "").strip()
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET", "").strip()
DEFAULT_INSTRUMENT = os.getenv("CRYPTO_SYMBOL", "BTC").strip()
IS_VERCEL = os.getenv("VERCEL") == "1" or os.getenv("VERCEL_ENV") is not None
DEFAULT_DB = "/tmp/crypto_option_chain.db" if IS_VERCEL else "crypto_option_chain.db"
DEFAULT_CSV = "/tmp/crypto_option_snapshots.csv" if IS_VERCEL else "crypto_option_snapshots.csv"
DB_PATH = os.getenv("DB_PATH", DEFAULT_DB).strip()
CSV_PATH = os.getenv("CSV_PATH", DEFAULT_CSV).strip()

app = FastAPI(title="Ashish Goswami LTP Calculator Pro - Crypto (Delta Exchange)")


@app.middleware("http")
async def fix_vercel_routing(request, call_next):
    query_path = request.query_params.get("path")
    if query_path is not None and query_path.strip() != "":
        target_path = "/" + query_path.strip().lstrip("/")
        request.scope["path"] = target_path
    elif request.headers.get("x-forwarded-uri"):
        raw_uri = request.headers.get("x-forwarded-uri")
        path_only = raw_uri.split("?")[0]
        if path_only in ["/api/index.py", "/api/index", "/api", "/api/"]:
            request.scope["path"] = "/"
        else:
            request.scope["path"] = path_only
    else:
        p = request.scope.get("path", "")
        if p in ["/api/index.py", "/api/index", "/api", "/api/"]:
            request.scope["path"] = "/"
    response = await call_next(request)
    return response

service = DeltaExchangeService(DELTA_API_KEY, DELTA_API_SECRET)
db = OptionChainDB(DB_PATH)
calc_engine = OptionChainCalculationEngine()
alert_engine = OptionChainAlertEngine(proximity_threshold=50.0)
whatsapp_service = WhatsAppAlertService(db=db)
telegram_service = TelegramAlertService(db=db, service=service, calc_engine=calc_engine)
audit_engine = TradeAuditEngine(db, service, calc_engine, buffer_pct=0.003, cooldown_minutes=60, whatsapp_service=whatsapp_service, telegram_service=telegram_service)

try:
    audit_engine.seed_historical_audit_records_if_empty()
except Exception as _e:
    print(f"[WARNING] Audit seeding warning: {_e}")

# Start background workers (Audit evaluation + Telegram interactive command bot listener)
if not IS_VERCEL:
    audit_engine.start_background_worker(interval_seconds=180)
    telegram_service.start_bot_listener()


@app.get("/api/expiries")
def get_expiries(instrument: str = Query(DEFAULT_INSTRUMENT)):
    """API endpoint to get upcoming expiry dates for specified Crypto asset (BTC, ETH)."""
    try:
        symbol_prefix = instrument.replace("DELTA_", "").replace("NSE_INDEX|", "").split(" ")[0].upper()
        if symbol_prefix not in ["BTC", "ETH", "SOL"]:
            symbol_prefix = "BTC"
        expiries = service.get_upcoming_expiries(symbol_prefix)
        return {"status": "success", "expiries": expiries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/spot-price")
def get_spot_price_api(instrument: str = Query(DEFAULT_INSTRUMENT)):
    """
    DUAL-CYCLE FAST ENDPOINT (2-3 sec poll):
    Returns fast live spot price for real-time ticker and chart sync.
    """
    try:
        symbol_prefix = instrument.replace("DELTA_", "").replace("NSE_INDEX|", "").split(" ")[0].upper()
        if symbol_prefix not in ["BTC", "ETH", "SOL"]:
            symbol_prefix = "BTC"
        _, meta = service.fetch_option_chain_snapshot(symbol_prefix=symbol_prefix)
        spot = meta.get("spot_price", 0.0)
        now_str = get_ist_str()
        return {"status": "success", "spot_price": spot, "timestamp": now_str}
    except Exception as e:
        return {"status": "error", "spot_price": 0.0, "detail": str(e)}


@app.get("/api/tts-audio")
def get_tts_audio(text: str = Query("सावधान! क्रिप्टो का रिवर्सल लेवल आ गया है")):
    """
    Generates real MP3 Hindi Voice Stream using Google TTS.
    Returns 100% Pure Hindi Audio (no English mixing!).
    """
    try:
        tts = gTTS(text=text, lang="hi", slow=False)
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        fp.seek(0)
        return StreamingResponse(fp, media_type="audio/mp3")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/historical-snapshots")
def get_historical_snapshots(limit: int = Query(50)):
    """Returns list of recorded historical snapshots in SQLite database."""
    try:
        df = db.list_snapshots(limit=limit)
        snapshots = df.to_dict(orient="records")
        return {"status": "success", "snapshots": snapshots}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/historical-snapshot-detail")
def get_historical_snapshot_detail(snapshot_id: str = Query(...)):
    """Fetches details for a specific historical snapshot ID for Time-Travel view."""
    try:
        df_records = db.get_snapshot(snapshot_id)
        df_analysis = db.get_snapshot_analysis(snapshot_id)

        if df_records.empty:
            raise HTTPException(status_code=444, detail="Snapshot not found")

        records = df_records.to_dict(orient="records")
        spot = df_records["spot_price"].iloc[0]
        expiry = df_records["expiry_date"].iloc[0]

        analysis_res = calc_engine.analyze(records, spot, expiry)
        meta = {
            "timestamp": df_records["timestamp"].iloc[0],
            "snapshot_id": snapshot_id,
            "instrument_key": df_records["instrument_key"].iloc[0],
            "expiry_date": expiry,
            "spot_price": spot
        }

        return {
            "status": "success",
            "meta": meta,
            "analysis": {
                "resistance": analysis_res.get("resistance"),
                "support": analysis_res.get("support"),
                "coa_scenario": analysis_res.get("coa_scenario"),
                "coa2_confirmation": analysis_res.get("coa2_confirmation"),
                "buyer_directive": analysis_res.get("buyer_directive"),
                "eos": analysis_res.get("eos"),
                "eor": analysis_res.get("eor"),
                "ur": analysis_res.get("ur"),
                "us": analysis_res.get("us"),
                "soc_detected": analysis_res.get("soc_detected"),
                "soc_message": analysis_res.get("soc_message")
            },
            "alerts": [],
            "records": analysis_res.get("records_with_reversals", records)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/chart-candles")
def get_chart_candles(instrument: str = Query(DEFAULT_INSTRUMENT), resolution: str = Query("1"), limit: int = Query(100)):
    """API endpoint returning authentic historical OHLC candles directly from Delta Exchange API."""
    try:
        symbol_prefix = instrument.replace("DELTA_", "").replace("NSE_INDEX|", "").split(" ")[0].upper()
        candles = service.get_candles(symbol_prefix=symbol_prefix, resolution=resolution, limit=limit)
        if not candles:
            candles = db.get_candles(instrument, limit=limit)
        return {"status": "success", "candles": candles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/920-strategy")
def get_920_strategy_api(
    instrument: str = Query(DEFAULT_INSTRUMENT),
    target_date: Optional[str] = Query(None)
):
    """
    STRICT 9:20 AM MORNING REVERSAL STRATEGY RETRIEVAL API:
    Queries SQLite database for the exact 09:20:00 AM IST base snapshot for target_date.
    Does NOT fallback to current live snapshot if 09:20 AM snapshot is not in database.
    """
    try:
        records_920, meta_920 = db.get_920_snapshot(instrument_key=instrument, target_date=target_date)
        if not records_920 or not meta_920:
            return {
                "status": "not_available",
                "message": "⚠️ 9:20 AM Historical Snapshot Not Available in Database - Please refer to Live COA Mode",
                "meta_920": None,
                "strategy_920": None
            }

        res_920 = calc_engine.analyze_920_strategy(records_920, meta_920["spot_price"], meta_920["expiry_date"])

        return {
            "status": "success",
            "meta_920": meta_920,
            "strategy_920": res_920
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/option-chain")
def get_option_chain_data(
    instrument: str = Query(DEFAULT_INSTRUMENT),
    expiry: Optional[str] = Query(None)
):
    """
    DUAL-CYCLE SNAPSHOT ENDPOINT (60 sec cycle):
    Fetches full Crypto option chain from Delta Exchange, calculates COA 1.0/2.0 rules, evaluates Smart Event Voice Alerts, and logs to SQLite.
    """
    try:
        symbol_prefix = instrument.replace("DELTA_", "").replace("NSE_INDEX|", "").split(" ")[0].upper()
        if symbol_prefix not in ["BTC", "ETH", "SOL"]:
            symbol_prefix = "BTC"

        records, meta = service.fetch_option_chain_snapshot(symbol_prefix=symbol_prefix, expiry_date=expiry)
        analysis_res = calc_engine.analyze(records, meta["spot_price"], meta["expiry_date"])
        records_with_reversals = analysis_res.get("records_with_reversals", records)
        alerts = alert_engine.evaluate_alerts(meta["spot_price"], records_with_reversals, analysis_res)

        db.save_snapshot(records_with_reversals, analysis_res=analysis_res, csv_path=CSV_PATH)

        # Dual-protection: Check setup triggers & evaluate active trades on poll cycle
        try:
            audit_engine.check_and_trigger_setups(symbol_prefix)
            audit_engine.evaluate_active_trades(symbol_prefix)
        except Exception as _ae:
            print(f"[WARNING] On-poll audit check warning: {_ae}")

        return {
            "status": "success",
            "meta": meta,
            "analysis": {
                "spot_price": meta.get("spot_price", 0.0),
                "resistance": analysis_res.get("resistance"),
                "support": analysis_res.get("support"),
                "coa_scenario": analysis_res.get("coa_scenario"),
                "coa2_confirmation": analysis_res.get("coa2_confirmation"),
                "buyer_directive": analysis_res.get("buyer_directive"),
                "diversion_strategy": analysis_res.get("diversion_strategy"),
                "eos": analysis_res.get("eos"),
                "eor": analysis_res.get("eor"),
                "ur": analysis_res.get("ur"),
                "us": analysis_res.get("us"),
                "soc_detected": analysis_res.get("soc_detected"),
                "soc_message": analysis_res.get("soc_message")
            },
            "alerts": alerts,
            "records": records_with_reversals
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trade-audit")
def get_trade_audit_endpoint(
    date: Optional[str] = Query(None),
    asset: Optional[str] = Query(None),
    evaluate_now: bool = Query(True)
):
    """
    Automated Trade Audit & Performance Tracker API:
    Returns performance summary metrics (Setups, Target Hits, SL Hits, Win Rate),
    distinct dates list, and detailed historical audit logs.
    """
    try:
        if evaluate_now:
            audit_engine.evaluate_active_trades(asset=asset)
        data = audit_engine.get_dashboard_audit_data(date_filter=date, asset=asset)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/trade-audit/trigger-check")
@app.get("/api/trade-audit/trigger-check")
def trigger_trade_audit_check(asset: Optional[str] = Query(None)):
    """
    Manually forces background level trigger check and active trade evaluation.
    """
    try:
        assets = [asset.upper()] if asset else ["BTC", "ETH"]
        audit_engine.evaluate_and_log_all_assets(assets=assets)
        data = audit_engine.get_dashboard_audit_data(asset=asset)
        return {"status": "success", "message": "Audit evaluation completed", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/favicon.ico")
def get_favicon():
    return JSONResponse(content={}, status_code=204)


class WhatsAppConfigPayload(BaseModel):
    phone: Optional[str] = None
    apikey: Optional[str] = None
    enabled: Optional[bool] = None
    webhook_url: Optional[str] = None


@app.get("/api/whatsapp/config")
def get_whatsapp_config_endpoint():
    """Returns current WhatsApp alert configuration (CallMeBot & Webhook) with masked API key."""
    try:
        cfg = whatsapp_service.get_config()
        return {"status": "success", "config": cfg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/whatsapp/config")
def update_whatsapp_config_endpoint(payload: WhatsAppConfigPayload):
    """Updates WhatsApp phone number, CallMeBot API key, enabled status, and optional webhook."""
    try:
        updated = whatsapp_service.update_config(
            phone=payload.phone,
            apikey=payload.apikey,
            enabled=payload.enabled,
            webhook_url=payload.webhook_url
        )
        return {"status": "success", "message": "WhatsApp configuration saved successfully", "config": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/whatsapp/test")
def test_whatsapp_alert_endpoint():
    """Sends a test WhatsApp alert via CallMeBot API / Webhook to verify credentials."""
    try:
        result = whatsapp_service.send_test_alert()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class TelegramConfigPayload(BaseModel):
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    enabled: Optional[bool] = None


@app.get("/api/telegram/config")
def get_telegram_config_endpoint():
    """Returns current Telegram alert configuration with masked Bot Token."""
    try:
        cfg = telegram_service.get_config()
        return {"status": "success", "config": cfg}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/telegram/config")
def update_telegram_config_endpoint(payload: TelegramConfigPayload):
    """Updates Telegram bot token, chat ID, and enabled status."""
    try:
        updated = telegram_service.update_config(
            bot_token=payload.bot_token,
            chat_id=payload.chat_id,
            enabled=payload.enabled
        )
        return {"status": "success", "message": "Telegram configuration saved successfully", "config": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/telegram/test")
def test_telegram_alert_endpoint():
    """Sends a test Telegram alert via official Bot API to verify credentials."""
    try:
        result = telegram_service.send_test_alert()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/telegram/send-status")
@app.get("/api/telegram/send-status")
def send_telegram_live_status_endpoint(asset: str = Query("BTC")):
    """Sends on-demand live market snapshot (Spot, EOS, EOR, Support/Resistance) to Telegram."""
    try:
        result = telegram_service.send_live_snapshot(asset=asset)
        return {"status": "success", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



_DASHBOARD_HTML_CACHE = None

def get_dashboard_html() -> str:
    global _DASHBOARD_HTML_CACHE
    if _DASHBOARD_HTML_CACHE is not None:
        return _DASHBOARD_HTML_CACHE
    candidate_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "api", "dashboard.html"),
        os.path.join(os.getcwd(), "dashboard.html"),
        os.path.join(os.getcwd(), "api", "dashboard.html"),
    ]
    for p in candidate_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    _DASHBOARD_HTML_CACHE = f.read()
                    print(f"[INFO] Loaded dashboard.html from {p} ({len(_DASHBOARD_HTML_CACHE)} bytes)")
                    return _DASHBOARD_HTML_CACHE
            except Exception as e:
                print(f"[ERROR] Failed reading {p}: {e}")
    return "<html><body><h2>LogicTrader Crypto Terminal</h2><p>dashboard.html not found. Check deployment path.</p></body></html>"


@app.get("/", response_class=HTMLResponse)
@app.get("/api/index.py", response_class=HTMLResponse)
@app.get("/api/index", response_class=HTMLResponse)
@app.get("/api", response_class=HTMLResponse)
def serve_dashboard():
    """Serves main LogicTrader web dashboard HTML page."""
    return HTMLResponse(content=get_dashboard_html())



if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"[INFO] Launching Ashish Goswami LTP Calculator Pro Dashboard on http://localhost:{port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
