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
    raw_path = request.headers.get("x-forwarded-uri", "").split("?")[0]
    if raw_path:
        request.scope["path"] = raw_path
    else:
        path = request.scope.get("path", "")
        if path in ["/api/index.py", "/api/index", "/api", "/api/"]:
            request.scope["path"] = "/"
    response = await call_next(request)
    return response

service = DeltaExchangeService(DELTA_API_KEY, DELTA_API_SECRET)
db = OptionChainDB(DB_PATH)
calc_engine = OptionChainCalculationEngine()
alert_engine = OptionChainAlertEngine(proximity_threshold=50.0)


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
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
def get_chart_candles(instrument: str = Query(DEFAULT_INSTRUMENT), limit: int = Query(100)):
    """API endpoint returning historical 1-minute OHLC candles for TradingView Lightweight Charts."""
    try:
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
            "alerts": alerts,
            "records": records_with_reversals
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/", response_class=HTMLResponse)
@app.get("/api/index.py", response_class=HTMLResponse)
@app.get("/api", response_class=HTMLResponse)
def serve_dashboard():
    """Serves main web dashboard HTML page with Lightweight Chart IST TimeZone, 9:20 Strict DB Lock, and COA 1.0/2.0 Engine."""
    html_content = """<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ashish Goswami LTP Calculator Pro</title>
    <!-- Bootstrap 5 CSS -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
    <!-- FontAwesome Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.2/css/all.min.css">
    <!-- TradingView Lightweight Charts Standalone CDN -->
    <script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: 'Segoe UI', system-ui, sans-serif; }
        .card-custom { background: #1e293b; border: 1px solid #334155; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }
        .badge-scenario { font-size: 1.1rem; padding: 8px 16px; border-radius: 8px; }
        
        /* Table Formatting */
        .table-dark-custom { color: #f8fafc; font-size: 0.9rem; }
        .table-dark-custom th { background-color: #0f172a; color: #94a3b8; border-color: #334155; text-align: center; }
        .table-dark-custom td { border-color: #334155; vertical-align: middle; }
        
        /* Highlight Colors & Heatmaps */
        .bg-support { background-color: rgba(25, 135, 84, 0.25) !important; border-left: 4px solid #198754; }
        .bg-resistance { background-color: rgba(220, 53, 69, 0.25) !important; border-right: 4px solid #dc3545; }
        .bg-wtt-wtb { background-color: rgba(255, 193, 7, 0.2) !important; }
        .bg-atm { background-color: rgba(13, 110, 253, 0.25) !important; font-weight: bold; }
        .bg-itm-tint { background-color: rgba(245, 158, 11, 0.08) !important; }
        
        /* Cell Highest & WTT Highlights */
        .cell-highest-res { background-color: #dc3545 !important; color: #ffffff !important; font-weight: bold; }
        .cell-highest-supp { background-color: #198754 !important; color: #ffffff !important; font-weight: bold; }
        .cell-second-wtt-wtb { background-color: #ffc107 !important; color: #000000 !important; font-weight: bold; }

        /* Badges */
        .badge-supp { background-color: #198754; color: #fff; }
        .badge-res { background-color: #dc3545; color: #fff; }
        .badge-alert { background-color: #ffc107; color: #000; font-weight: bold; }
        .badge-atm-tag { background-color: #0d6efd; color: #fff; }

        /* Progress Bar */
        .progress-thin { height: 4px; background-color: #334155; }
        
        /* Floating Toast Container */
        #toast-container { position: fixed; top: 20px; right: 20px; z-index: 9999; width: 350px; }
        
        /* Custom Tooltip Styling */
        .tooltip-inner { background-color: #0284c7; color: #fff; max-width: 320px; font-size: 0.85rem; padding: 10px; border-radius: 8px; text-align: left; }
    </style>
</head>
<body class="p-2 p-md-3">

<!-- Floating Toast Alerts Container -->
<div id="toast-container"></div>

<div class="container-fluid">

    <!-- State of Confusion (SOC) Alert Banner -->
    <div id="soc-alert-banner" class="alert alert-danger d-none align-items-center mb-3 p-3 text-center fs-5 fw-bold" role="alert" style="background-color: #991b1b; color: #fff; border: 2px solid #ef4444;">
        <i class="fa-solid fa-triangle-exclamation me-3 fs-3"></i>
        <span id="soc-alert-text">⚠️ STATE OF CONFUSION DETECTED - NO TRADE ZONE!</span>
    </div>

    <!-- Header Controls Bar -->
    <div class="card-custom p-3 mb-3">
        <div class="d-flex flex-row justify-content-between align-items-center flex-wrap gap-2 mb-2">
            <div class="d-flex align-items-center gap-3">
                <h4 class="m-0 text-info fs-5 fs-md-4"><i class="fa-brands fa-bitcoin me-2"></i>Ashish Goswami LTP Calculator Pro - Crypto (Delta Exchange)</h4>
                <span id="last-updated" class="badge bg-secondary">Updating...</span>
                <span id="mode-badge" class="badge bg-success">LIVE MODE</span>
            </div>
            
            <div class="d-flex align-items-center gap-2 flex-wrap">
                <!-- Strategy Mode Switcher Controller -->
                <div class="btn-group btn-group-sm" role="group" id="strategy-mode-group">
                    <button type="button" class="btn btn-outline-warning active" id="btn-mode-live" onclick="setStrategyMode('live')">
                        <i class="fa-solid fa-bolt me-1"></i> ⚡ Live COA 1.0 / 2.0 Mode
                    </button>
                    <button type="button" class="btn btn-outline-warning" id="btn-mode-920" onclick="setStrategyMode('920')">
                        <i class="fa-solid fa-sun me-1"></i> 🌅 9:20 AM Morning Strategy Mode
                    </button>
                </div>

                <!-- Test Hindi Voice Button -->
                <button id="btn-test-voice" class="btn btn-warning btn-sm text-dark font-weight-bold">
                    <i class="fa-solid fa-circle-play me-1"></i> 🔊 MP3 हिंदी वॉइस
                </button>

                <!-- UI Smart Voice Status Indicator Button -->
                <button id="btn-voice-toggle" class="btn btn-outline-warning btn-sm" title="Smart Event-Only Voice Alerts are active">
                    <i class="fa-solid fa-volume-high me-1"></i> 🔊 Event Voice: Active
                </button>

                <select id="instrument-select" class="form-select form-select-sm bg-dark text-light border-secondary" style="width: 210px;">
                    <option value="BTC" selected>BTC Options (Bitcoin)</option>
                    <option value="ETH">ETH Options (Ethereum)</option>
                    <option value="SOL">SOL Options (Solana)</option>
                </select>
                
                <select id="expiry-select" class="form-select form-select-sm bg-dark text-light border-secondary" style="width: 120px;">
                    <option value="">Auto Nearest</option>
                </select>
                
                <button id="btn-refresh" class="btn btn-primary btn-sm"><i class="fa-solid fa-rotate me-1"></i> Refresh</button>
            </div>
        </div>

        <!-- Dual View Mode Toggles & Time-Travel Controller -->
        <div class="d-flex align-items-center justify-content-between flex-wrap gap-2 mt-2 pt-2 border-top border-secondary">
            <div class="d-flex align-items-center gap-2 flex-wrap">
                <span class="text-info fw-bold small"><i class="fa-solid fa-desktop me-1"></i> Dashboard View:</span>
                <div class="btn-group btn-group-sm" role="group" id="view-mode-toggle-group">
                    <button type="button" class="btn btn-outline-info" id="btn-view-chart" onclick="setDashboardView('chart')">
                        <i class="fa-solid fa-chart-candlestick me-1"></i> 📊 Only Chart View
                    </button>
                    <button type="button" class="btn btn-outline-info" id="btn-view-chain" onclick="setDashboardView('chain')">
                        <i class="fa-solid fa-table me-1"></i> 📋 Only Option Chain View
                    </button>
                    <button type="button" class="btn btn-info active" id="btn-view-split" onclick="setDashboardView('split')">
                        <i class="fa-solid fa-bolt me-1"></i> ⚡ Split View (Both)
                    </button>
                </div>
            </div>

            <!-- Time-Travel Slider & Dropdown Bar -->
            <div class="d-flex align-items-center gap-2 flex-wrap">
                <span class="text-warning small fw-bold"><i class="fa-solid fa-clock-rotate-left me-1"></i> Time-Travel:</span>
                <select id="time-travel-select" class="form-select form-select-sm bg-dark text-warning border-warning" style="width: 230px;">
                    <option value="LIVE">🔴 LIVE REAL-TIME FEED</option>
                </select>
                <button id="btn-reset-live" class="btn btn-outline-info btn-sm">🔴 Reset to LIVE</button>
            </div>
        </div>
    </div>

    <!-- Live Timer Progress Bar -->
    <div class="progress progress-thin mb-3">
        <div id="refresh-progress" class="progress-bar bg-info" style="width: 100%;"></div>
    </div>

    <!-- DEDICATED 9:20 AM MORNING STRATEGY PANEL (Visible when 9:20 Mode active) -->
    <div class="card-custom p-3 mb-3 border-warning d-none" id="card-920-setup" style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);">
        <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2 border-bottom border-secondary pb-2">
            <div class="d-flex align-items-center gap-2">
                <i class="fa-solid fa-sun text-warning fs-4"></i>
                <div>
                    <h5 class="m-0 text-warning fw-bold">🌅 Dr. Vinay Prakash Tiwari 9:20 AM Morning Reversal Strategy Panel</h5>
                    <small class="text-muted" id="text-920-timestamp">Base Frozen Snapshot: Locked @ 09:20:00 AM IST</small>
                </div>
            </div>
            <span id="badge-920-action" class="badge bg-success fs-6 p-2 px-3">9:20 SETUP ACTIVE</span>
        </div>

        <div class="row g-2 text-center">
            <!-- Frozen Spot @ 9:20 -->
            <div class="col-6 col-md-2">
                <div class="p-2 rounded bg-dark border border-secondary h-100">
                    <small class="text-muted fw-bold d-block" style="font-size:0.75rem;">9:20 Frozen Spot</small>
                    <div id="val-920-spot" class="fw-bold text-info fs-6 mt-1">-</div>
                    <small class="text-muted d-block" style="font-size:0.65rem;">(Base Price)</small>
                </div>
            </div>
            <!-- 9:20 R2 (Upper Resistance) -->
            <div class="col-6 col-md-2">
                <div class="p-2 rounded bg-dark border border-warning h-100">
                    <small class="text-warning fw-bold d-block" style="font-size:0.75rem;">9:20 R2 (Upper Res)</small>
                    <div id="val-920-r2" class="fw-bold text-warning fs-6 mt-1">-</div>
                    <small id="sub-920-r2-strike" class="text-muted d-block" style="font-size:0.65rem;">R+1 Call Div</small>
                </div>
            </div>
            <!-- 9:20 R1 (Primary Resistance / EOR) -->
            <div class="col-6 col-md-2">
                <div class="p-2 rounded bg-dark border border-danger h-100">
                    <small class="text-danger fw-bold d-block" style="font-size:0.75rem;">9:20 R1 (EOR)</small>
                    <div id="val-920-r1" class="fw-bold text-danger fs-6 mt-1">-</div>
                    <small id="sub-920-res-strike" class="text-muted d-block" style="font-size:0.65rem;">Res Strike: -</small>
                </div>
            </div>
            <!-- 9:20 Mid-Pivot (Center Target) -->
            <div class="col-6 col-md-2">
                <div class="p-2 rounded bg-dark border border-light h-100">
                    <small class="text-light fw-bold d-block" style="font-size:0.75rem;">9:20 Mid-Pivot</small>
                    <div id="val-920-mid" class="fw-bold text-warning fs-6 mt-1">-</div>
                    <small class="text-muted d-block" style="font-size:0.65rem;">Target Pivot</small>
                </div>
            </div>
            <!-- 9:20 S1 (Primary Support / EOS) -->
            <div class="col-6 col-md-2">
                <div class="p-2 rounded bg-dark border border-success h-100">
                    <small class="text-success fw-bold d-block" style="font-size:0.75rem;">9:20 S1 (EOS)</small>
                    <div id="val-920-s1" class="fw-bold text-success fs-6 mt-1">-</div>
                    <small id="sub-920-supp-strike" class="text-muted d-block" style="font-size:0.65rem;">Supp Strike: -</small>
                </div>
            </div>
            <!-- 9:20 S2 (Lower Support / Deep Support) -->
            <div class="col-6 col-md-2">
                <div class="p-2 rounded bg-dark border border-info h-100">
                    <small class="text-info fw-bold d-block" style="font-size:0.75rem;">9:20 S2 (Deep Supp)</small>
                    <div id="val-920-s2" class="fw-bold text-info fs-6 mt-1">-</div>
                    <small id="sub-920-s2-strike" class="text-muted d-block" style="font-size:0.65rem;">S-1 Put Div</small>
                </div>
            </div>
        </div>

        <div class="alert alert-dark border-secondary py-2 px-3 m-0 mt-3 small text-light d-flex justify-content-between align-items-center flex-wrap gap-2">
            <span><i class="fa-solid fa-circle-info text-info me-2"></i><strong id="text-920-status-msg">9:20 AM Base Levels Locked. Watch for morning retest near 9:20 EOS / EOR levels.</strong></span>
            <span class="badge bg-secondary font-monospace" id="tag-920-lock-time">LOCKED @ 09:20 AM</span>
        </div>
    </div>

    <!-- COA 2.0 Confirmation & IV Trap Filter Status Bar -->
    <div class="card-custom p-2 px-3 mb-3 border-secondary d-flex flex-row justify-content-between align-items-center flex-wrap gap-2" id="coa2-container">
        <div class="d-flex align-items-center gap-2">
            <i class="fa-solid fa-shield-virus fs-5 text-warning"></i>
            <div>
                <strong class="text-light small">COA 2.0 Confirmation & IV Trap Filter:</strong>
                <span id="coa2-summary" class="text-muted small ms-2">Calculating IV comparative bias...</span>
            </div>
        </div>
        <span id="coa2-badge" class="badge bg-secondary">COA 2.0: Checking IV</span>
    </div>

    <!-- Summary & Directives Row -->
    <div class="row g-3 mb-3" id="live-summary-cards-row">
        <!-- Scenario & State Card -->
        <div class="col-md-4">
            <div class="card-custom p-3 h-100 border-primary">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="text-uppercase text-muted fw-bold small"><i class="fa-solid fa-chart-line me-1"></i>COA 1.0 Scenario</span>
                    <span id="badge-scenario-num" class="badge bg-primary badge-scenario">Scenario -</span>
                </div>
                <h5 id="scenario-name" class="text-warning mb-2 fs-6">Loading Scenario...</h5>
                <p id="scenario-state" class="mb-1 text-light fw-semibold small"></p>
                <div class="alert alert-info py-2 px-3 m-0 mt-2 small" role="alert" id="scenario-action">
                    <i class="fa-solid fa-compass me-1"></i><strong>Action:</strong> Strategy loading...
                </div>
            </div>
        </div>

        <!-- Option Buyer Directives Card (Strike, Entry, Exit, SL) -->
        <div class="col-md-4">
            <div class="card-custom p-3 h-100 border-warning" style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    <span class="text-warning fw-bold text-uppercase small"><i class="fa-solid fa-user-ninja me-1"></i>Option Buyer Directives</span>
                    <span id="buyer-badge" class="badge bg-success">BUY CALL (CE)</span>
                </div>
                <div class="bg-dark p-2 rounded border border-secondary mb-1">
                    <div class="d-flex justify-content-between align-items-center">
                        <small class="text-muted fw-bold me-2">1. स्ट्राइक कौनसी खरीदें:</small>
                        <span id="buyer-strike" class="badge bg-primary fs-6">Loading...</span>
                    </div>
                </div>
                <div class="bg-dark p-2 rounded border border-secondary mb-1">
                    <div class="d-flex justify-content-between align-items-center">
                        <small class="text-muted fw-bold me-2">2. एंट्री किस पॉइंट पर लें:</small>
                        <span id="buyer-entry" class="text-success fw-bold small">Loading...</span>
                    </div>
                </div>
                <div class="bg-dark p-2 rounded border border-secondary mb-1">
                    <div class="d-flex justify-content-between align-items-center">
                        <small class="text-muted fw-bold me-2">3. एग्जिट कहाँ पर करें:</small>
                        <span id="buyer-exit" class="text-danger fw-bold small">Loading...</span>
                    </div>
                </div>
                <div class="bg-dark p-2 rounded border border-secondary">
                    <div class="d-flex justify-content-between align-items-center">
                        <small class="text-muted fw-bold me-2">4. स्टॉप लॉस (Calculated SL):</small>
                        <span id="buyer-sl" class="text-warning fw-bold small">Loading...</span>
                    </div>
                </div>
            </div>
        </div>

        <!-- Support & Resistance Status Card -->
        <div class="col-md-4">
            <div class="card-custom p-3 h-100">
                <div class="row g-2 text-center">
                    <div class="col-6">
                        <div class="p-2 rounded bg-dark border border-danger">
                            <small class="text-danger fw-bold"><i class="fa-solid fa-hand me-1"></i>RESISTANCE (Call)</small>
                            <h5 id="res-strike" class="m-0 text-light fs-6">-</h5>
                            <span id="res-status-badge" class="badge bg-secondary">STRONG</span>
                            <div id="res-velocity" class="small mt-1 badge bg-secondary" style="font-size: 0.65rem;">Velocity: Stable</div>
                            <div id="res-details" class="small text-muted mt-1" style="font-size: 0.75rem;">Vol: - | OI: -</div>
                        </div>
                    </div>
                    <div class="col-6">
                        <div class="p-2 rounded bg-dark border border-success">
                            <small class="text-success fw-bold"><i class="fa-solid fa-shield-halved me-1"></i>SUPPORT (Put)</small>
                            <h5 id="supp-strike" class="m-0 text-light fs-6">-</h5>
                            <span id="supp-status-badge" class="badge bg-secondary">STRONG</span>
                            <div id="supp-velocity" class="small mt-1 badge bg-secondary" style="font-size: 0.65rem;">Velocity: Stable</div>
                            <div id="supp-details" class="small text-muted mt-1" style="font-size: 0.75rem;">Vol: - | OI: -</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Key Reversal & Market Metrics Bar -->
    <div class="card-custom p-3 mb-3 border-info" id="live-reversal-metrics-bar">
        <h6 class="text-info fw-bold mb-2"><i class="fa-solid fa-layer-group me-2"></i>लाइव मार्केट लेवल्स & Theoretical Reversal Engine (LTP + Black-Scholes)</h6>
        <div class="row g-2 text-center">
            <!-- Spot Price -->
            <div class="col-6 col-sm-4 col-md">
                <div class="p-2 rounded bg-dark border border-info h-100">
                    <small class="text-info fw-bold d-block">Spot Price</small>
                    <small class="text-muted d-block" style="font-size:0.7rem;">(क्रिप्टो / बिटकॉइन लाइव भाव)</small>
                    <div id="spot-price" class="fw-bold text-info fs-5 mt-1">-</div>
                </div>
            </div>
            <!-- ATM Strike -->
            <div class="col-6 col-sm-4 col-md">
                <div class="p-2 rounded bg-dark border border-warning h-100">
                    <small class="text-warning fw-bold d-block">ATM Strike</small>
                    <small class="text-muted d-block" style="font-size:0.7rem;">(एट द मनी स्ट्राइक)</small>
                    <div id="atm-strike" class="fw-bold text-warning fs-5 mt-1">-</div>
                </div>
            </div>
            <!-- EOS -->
            <div class="col-6 col-sm-4 col-md">
                <div class="p-2 rounded bg-dark border border-success h-100">
                    <small class="text-success fw-bold d-block">EOS (Support)</small>
                    <small class="text-muted d-block" style="font-size:0.7rem;">(सपोर्ट रिवर्सल)</small>
                    <div id="eos-price" class="fw-bold text-success fs-5 mt-1">-</div>
                    <small id="eos-bs" class="text-muted d-block mt-1" style="font-size:0.68rem;">BS: -</small>
                </div>
            </div>
            <!-- EOR -->
            <div class="col-6 col-sm-4 col-md">
                <div class="p-2 rounded bg-dark border border-danger h-100">
                    <small class="text-danger fw-bold d-block">EOR (Resistance)</small>
                    <small class="text-muted d-block" style="font-size:0.7rem;">(रेजिस्टेंस रिवर्सल)</small>
                    <div id="eor-price" class="fw-bold text-danger fs-5 mt-1">-</div>
                    <small id="eor-bs" class="text-muted d-block mt-1" style="font-size:0.68rem;">BS: -</small>
                </div>
            </div>
            <!-- UR -->
            <div class="col-6 col-sm-4 col-md">
                <div class="p-2 rounded bg-dark border border-warning h-100">
                    <small class="text-warning fw-bold d-block">UR (Upper Res)</small>
                    <small class="text-muted d-block" style="font-size:0.7rem;">(अपर रेजिस्टेंस Target)</small>
                    <div id="ur-price" class="fw-bold text-warning fs-5 mt-1">-</div>
                </div>
            </div>
            <!-- US -->
            <div class="col-6 col-sm-4 col-md">
                <div class="p-2 rounded bg-dark border border-info h-100">
                    <small class="text-info fw-bold d-block">US (Ult Support)</small>
                    <small class="text-muted d-block" style="font-size:0.7rem;">(अल्टीमेट सपोर्ट Target)</small>
                    <div id="us-price" class="fw-bold text-info fs-5 mt-1">-</div>
                </div>
            </div>
            <!-- PCR -->
            <div class="col-6 col-sm-4 col-md">
                <div class="p-2 rounded bg-dark border border-secondary h-100">
                    <small class="text-light fw-bold d-block">PCR</small>
                    <small class="text-muted d-block" style="font-size:0.7rem;">(पुट-कॉल रेशियो)</small>
                    <div id="pcr-val" class="fw-bold text-light fs-5 mt-1">-</div>
                </div>
            </div>
        </div>
    </div>

    <!-- MAIN DUAL VIEW SECTION: Chart Container + Option Chain Table -->
    <div class="row g-3 mb-3" id="main-view-wrapper">
        
        <!-- 1. Real-Time TradingView Lightweight Chart Container -->
        <div class="col-12 col-xl-6" id="chart-view-container">
            <div class="card-custom p-3 h-100 border-info">
                <div class="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-2">
                    <div class="d-flex align-items-center gap-2">
                        <h5 class="m-0 text-info fs-6 fs-md-5">
                            <i class="fa-solid fa-chart-candlestick me-2"></i>TradingView Chart & Reversal Levels (IST)
                        </h5>
                        <span id="chart-instrument-tag" class="badge bg-secondary">BTC</span>
                    </div>

                    <!-- Line Visibility Checkboxes Toolbar -->
                    <div class="d-flex align-items-center gap-2 flex-wrap bg-dark p-1 px-2 rounded border border-secondary" style="font-size: 0.75rem;" id="chart-line-controls-toolbar">
                        <span class="text-muted fw-bold me-1"><i class="fa-solid fa-eye me-1"></i>Lines:</span>
                        <label class="form-check-label text-success me-2" style="cursor:pointer;">
                            <input class="form-check-input me-1" type="checkbox" id="chk-eos-eor" checked onchange="toggleChartLines()"> EOS/EOR
                        </label>
                        <label class="form-check-label text-warning me-2" style="cursor:pointer;">
                            <input class="form-check-input me-1" type="checkbox" id="chk-ur-us" checked onchange="toggleChartLines()"> UR/US
                        </label>
                        <label class="form-check-label text-info me-2" style="cursor:pointer;">
                            <input class="form-check-input me-1" type="checkbox" id="chk-diversions" checked onchange="toggleChartLines()"> Diversions
                        </label>
                        <label class="form-check-label text-danger" style="cursor:pointer;">
                            <input class="form-check-input me-1" type="checkbox" id="chk-target-sl" checked onchange="toggleChartLines()"> Target/SL
                        </label>
                    </div>
                </div>

                <!-- Lightweight Chart Canvas Mount Point -->
                <div id="chart-container" style="width: 100%; height: 520px; position: relative; border-radius: 8px; overflow: hidden; background-color: #0f172a; border: 1px solid #334155;"></div>

                <div class="d-flex justify-content-between align-items-center mt-2 px-1 text-muted small">
                    <span><i class="fa-solid fa-circle-dot text-success me-1"></i> Live 2.5s Fast Spot Sync (IST Indian Market Time)</span>
                    <span id="chart-last-tick" class="fw-semibold text-info">Spot Tick: Waiting...</span>
                </div>
            </div>
        </div>

        <!-- 2. Option Chain Table Container -->
        <div class="col-12 col-xl-6" id="option-chain-view-container">
            <div class="card-custom p-3 h-100">
                <div class="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-1">
                    <h5 class="m-0 text-light fs-6 fs-md-5"><i class="fa-solid fa-table me-2"></i>LTP Calculator Option Chain</h5>
                    <div class="small">
                        <span class="badge badge-res me-1">🛑 Resistance</span>
                        <span class="badge badge-supp me-1">🛡️ Support</span>
                        <span class="badge badge-alert me-1">⚠️ WTT/WTB</span>
                        <span class="badge badge-atm-tag">🎯 ATM</span>
                    </div>
                </div>

                <div class="table-responsive">
                    <table class="table table-dark table-hover table-bordered table-dark-custom align-middle m-0" id="option-table">
                        <thead>
                            <tr>
                                <th class="table-danger" colspan="5">CALL OPTIONS (RESISTANCE SIDE)</th>
                                <th class="table-dark" style="width: 130px;">STRIKE</th>
                                <th class="table-success" colspan="5">PUT OPTIONS (SUPPORT SIDE)</th>
                            </tr>
                            <tr>
                                <th>OI</th>
                                <th>ΔOI</th>
                                <th>VOLUME</th>
                                <th>CALL DIV</th>
                                <th>LTP</th>
                                <th>STRIKE PRICE</th>
                                <th>LTP</th>
                                <th>PUT DIV</th>
                                <th>VOLUME</th>
                                <th>ΔOI</th>
                                <th>OI</th>
                            </tr>
                        </thead>
                        <tbody id="table-body">
                            <tr><td colspan="11" class="text-center py-4 text-muted">Loading live option chain data...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

    </div>
</div>

<!-- Bootstrap 5 JS & Popper -->
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>

<script>
    let countdownInterval = null;
    let spotTrackerInterval = null;
    let refreshSeconds = 60;
    let currentTimer = refreshSeconds;
    let voiceAlertsEnabled = true;
    let isTimeTravelMode = false;

    // Strategy & View Mode State
    let currentStrategyMode = "live"; // 'live' | '920'
    let activeViewMode = "split"; // 'chart' | 'chain' | 'split'
    let data920Cache = null;

    // Smart Voice Cooldown Filter (10 Minutes / 600s)
    const voiceCooldownSeconds = 600;
    const voiceLastPlayedMap = {};

    // Lightweight Chart Variables
    let chart = null;
    let candlestickSeries = null;
    let activePriceLines = [];
    let currentCandle = null;
    let lastDashboardData = null;

    function formatNumber(val) {
        if (!val && val !== 0) return "-";
        val = Number(val);
        if (Math.abs(val) >= 1000000) return (val / 1000000).toFixed(2) + "M";
        if (Math.abs(val) >= 1000) return (val / 1000).toFixed(1) + "K";
        return val.toFixed(0);
    }

    // DUAL-CYCLE FETCHING LOOP 1: 60-Second Full Snapshot Cycle
    function startTimer() {
        if (isTimeTravelMode) return;
        clearInterval(countdownInterval);
        currentTimer = refreshSeconds;
        updateProgressBar();
        countdownInterval = setInterval(() => {
            currentTimer--;
            updateProgressBar();
            if (currentTimer <= 0) {
                if (currentStrategyMode === "920") {
                    fetch920StrategyData();
                } else {
                    fetchOptionChainData();
                }
            }
        }, 1000);
    }

    // DUAL-CYCLE FETCHING LOOP 2: 2.5-Second Fast Spot Tracker Cycle
    function startSpotPriceTracker() {
        clearInterval(spotTrackerInterval);
        if (isTimeTravelMode) return;

        spotTrackerInterval = setInterval(async () => {
            const inst = document.getElementById("instrument-select").value;
            try {
                const res = await fetch(`/api/spot-price?instrument=${encodeURIComponent(inst)}`);
                const data = await res.json();
                if (data.status === "success" && data.spot_price > 0) {
                    updateLiveSpotPriceOnly(data.spot_price, data.timestamp);
                }
            } catch (e) {
                // Silent catch for fast poll
            }
        }, 2500);
    }

    function updateLiveSpotPriceOnly(spot, timestampStr) {
        // 1. Update Metric Spot Price Box
        const spotEl = document.getElementById("spot-price");
        if (spotEl) spotEl.innerText = spot.toFixed(2);

        // 2. Update TradingView Candle & Chart Tick Indicator
        updateChartCandle(spot, timestampStr);

        // 3. Update Floating Spot Price Ticker Text in Option Table
        const tickerEl = document.getElementById("floating-spot-ticker-text");
        if (tickerEl) {
            tickerEl.innerText = `─── 🎯 SPOT PRICE: ${spot.toFixed(2)} ▲ ───`;
        }

        // 4. Check 9:20 Reversal Level Proximity Announcement
        if (currentStrategyMode === "920" && data920Cache && data920Cache.status === "success") {
            check920ProximityVoiceAlert(spot);
        }
    }

    function updateProgressBar() {
        const pct = (currentTimer / refreshSeconds) * 100;
        document.getElementById("refresh-progress").style.width = pct + "%";
    }

    // Pure MP3 Hindi Audio Player with 10-Minute Anti-Spam Cooldown Filter
    function playHindiMP3(text, alertKey) {
        if (!voiceAlertsEnabled || !text) return;

        const key = alertKey || text;
        const now = Math.floor(Date.now() / 1000);
        const lastPlayed = voiceLastPlayedMap[key] || 0;

        if (now - lastPlayed < voiceCooldownSeconds) {
            console.log(`[AUDIO SILENCED - COOLDOWN ACTIVE] Alert: "${key}" (Remaining Cooldown: ${voiceCooldownSeconds - (now - lastPlayed)}s)`);
            return;
        }

        voiceLastPlayedMap[key] = now;

        try {
            const audioUrl = `/api/tts-audio?text=${encodeURIComponent(text)}`;
            const audio = new Audio(audioUrl);
            audio.play().catch(err => {
                console.warn("Audio play blocked by browser:", err);
            });
        } catch (e) {
            console.error("Audio playback error:", e);
        }
    }

    function testHindiVoice() {
        const sampleMsg = "सावधान! बिटकॉइन क्रिप्टो का रिवर्सल लेवल सपोर्ट के पास पहुँच गया है";
        triggerToastAlert("हिंदी MP3 वॉइस टेस्ट", "सावधान! बिटकॉइन क्रिप्टो का रिवर्सल लेवल पहुँच गया है", "DANGER");
        playHindiMP3(sampleMsg, "TEST_VOICE_KEY_" + Date.now());
    }

    function triggerToastAlert(title, message, severity) {
        const container = document.getElementById("toast-container");
        const bgClass = severity === "DANGER" ? "bg-danger text-white" : "bg-warning text-dark";
        const icon = severity === "DANGER" ? "fa-triangle-exclamation" : "fa-bell";

        const toastHTML = document.createElement("div");
        toastHTML.className = `toast align-items-center ${bgClass} border-0 show shadow mb-2`;
        toastHTML.setAttribute("role", "alert");
        toastHTML.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">
                    <i class="fa-solid ${icon} me-2"></i><strong>${title}</strong><br>${message}
                </div>
                <button type="button" class="btn-close me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        container.appendChild(toastHTML);
        
        setTimeout(() => {
            if (toastHTML.parentNode) toastHTML.parentNode.removeChild(toastHTML);
        }, 8000);
    }

    // ==========================================
    // STRATEGY MODE SELECTION & CONTROLLER
    // ==========================================
    function setStrategyMode(mode) {
        currentStrategyMode = mode;
        const btnLive = document.getElementById("btn-mode-live");
        const btn920 = document.getElementById("btn-mode-920");
        const card920 = document.getElementById("card-920-setup");
        const summaryCards = document.getElementById("live-summary-cards-row");
        const toolbar = document.getElementById("chart-line-controls-toolbar");
        const coa2Container = document.getElementById("coa2-container");

        btnLive.classList.remove("active");
        btn920.classList.remove("active");

        if (mode === "920") {
            btn920.classList.add("active");
            card920.classList.remove("d-none");
            if (summaryCards) summaryCards.classList.add("d-none");
            if (coa2Container) coa2Container.classList.add("d-none");
            if (toolbar) toolbar.classList.add("d-none");

            setDashboardView("split");
            fetch920StrategyData();
        } else {
            btnLive.classList.add("active");
            card920.classList.add("d-none");
            if (summaryCards) summaryCards.classList.remove("d-none");
            if (coa2Container) coa2Container.classList.remove("d-none");
            if (toolbar) toolbar.classList.remove("d-none");

            fetchOptionChainData();
        }
    }

    async function fetch920StrategyData() {
        const inst = document.getElementById("instrument-select").value;
        let url = `/api/920-strategy?instrument=${encodeURIComponent(inst)}`;

        try {
            const res = await fetch(url);
            const data = await res.json();
            data920Cache = data;

            if (data.status === "success") {
                render920StrategyPanel(data);
                render920ChartLines(data);
                announce920LockedVoice(data);
            } else {
                render920StrategyPanel(data);
            }
            startTimer();
        } catch (err) {
            console.error("Error fetching 9:20 strategy data:", err);
            render920StrategyPanel({ status: "not_available", message: "⚠️ 9:20 AM Historical Snapshot Not Available in Database - Please refer to Live COA Mode" });
        }
    }

    function render920StrategyPanel(data) {
        if (!data || data.status === "not_available" || !data.strategy_920) {
            const msg = (data && data.message) ? data.message : "⚠️ 9:20 AM Historical Snapshot Not Available in Database - Please refer to Live COA Mode";
            document.getElementById("text-920-timestamp").innerText = "Base Snapshot: NOT AVAILABLE IN DATABASE";
            document.getElementById("val-920-spot").innerText = "N/A";
            document.getElementById("val-920-r2").innerText = "N/A";
            document.getElementById("val-920-r1").innerText = "N/A";
            document.getElementById("sub-920-res-strike").innerText = "Res Strike: N/A";
            document.getElementById("val-920-mid").innerText = "N/A";
            document.getElementById("val-920-s1").innerText = "N/A";
            document.getElementById("sub-920-supp-strike").innerText = "Supp Strike: N/A";
            document.getElementById("val-920-s2").innerText = "N/A";

            const actionBadge = document.getElementById("badge-920-action");
            if (actionBadge) {
                actionBadge.innerText = "NO 9:20 SNAPSHOT IN DB";
                actionBadge.className = "badge bg-warning text-dark fs-6 p-2 px-3";
            }

            const msgEl = document.getElementById("text-920-status-msg");
            if (msgEl) msgEl.innerText = msg;

            document.getElementById("tag-920-lock-time").innerText = "SNAPSHOT MISSING";
            clearChartPriceLines();
            return;
        }

        const meta = data.meta_920 || {};
        const strat = data.strategy_920 || {};

        document.getElementById("text-920-timestamp").innerText = "Base Frozen Snapshot: Locked @ " + (meta.timestamp || "09:20:00 AM IST");
        document.getElementById("val-920-spot").innerText = (strat.spot_price_920 || meta.spot_price || 0).toFixed(2);

        document.getElementById("val-920-r2").innerText = (strat.r2_920 || 0).toFixed(2);
        document.getElementById("val-920-r1").innerText = (strat.r1_920 || strat.eor_920 || 0).toFixed(2);
        document.getElementById("sub-920-res-strike").innerText = "Res Strike: " + (strat.res_strike_920 ? strat.res_strike_920.toLocaleString() : "-");

        document.getElementById("val-920-mid").innerText = (strat.mid_920 || 0).toFixed(2);

        document.getElementById("val-920-s1").innerText = (strat.s1_920 || strat.eos_920 || 0).toFixed(2);
        document.getElementById("sub-920-supp-strike").innerText = "Supp Strike: " + (strat.supp_strike_920 ? strat.supp_strike_920.toLocaleString() : "-");
        document.getElementById("val-920-s2").innerText = (strat.s2_920 || 0).toFixed(2);

        const actionBadge = document.getElementById("badge-920-action");
        if (actionBadge) {
            actionBadge.innerText = strat.directive_badge || "9:20 SETUP ACTIVE";
            actionBadge.className = `badge ${strat.badge_class || 'bg-success'} fs-6 p-2 px-3`;
        }

        const msgEl = document.getElementById("text-920-status-msg");
        if (msgEl) msgEl.innerText = strat.status_msg || "9:20 AM Base Levels Locked.";

        document.getElementById("tag-920-lock-time").innerText = "LOCKED @ " + (meta.timestamp ? meta.timestamp.split(" ")[1] : "09:20 AM");
    }

    function announce920LockedVoice(data) {
        if (!data || !data.strategy_920) return;
        const s = data.strategy_920;
        const suppHindi = formatNumber(s.supp_strike_920);
        const resHindi = formatNumber(s.res_strike_920);
        const voiceText = `नौ बीस के लेवल्स लॉक हो गए हैं। सपोर्ट ${suppHindi} और रेजिस्टेंस ${resHindi} पर है। मॉर्निंग रिवर्सल का ध्यान रखें`;
        playHindiMP3(voiceText, `ANNOUNCE_920_LOCKED_${s.supp_strike_920}_${s.res_strike_920}`);
    }

    function check920ProximityVoiceAlert(spot) {
        if (!data920Cache || !data920Cache.strategy_920) return;
        const s = data920Cache.strategy_920;
        const eos = s.eos_920;
        const eor = s.eor_920;

        if (eos > 0 && Math.abs(spot - eos) <= 5.0) {
            const voiceText = `ध्यान दें! क्रिप्टो नौ बीस के मॉर्निंग रिवर्सल सपोर्ट ${eos.toFixed(0)} के पास पहुँच गया है। कॉल साइड ट्रेड एक्टिव है`;
            playHindiMP3(voiceText, `PROX_920_EOS_${Math.round(eos)}`);
        } else if (eor > 0 && Math.abs(spot - eor) <= 5.0) {
            const voiceText = `ध्यान दें! क्रिप्टो नौ बीस के मॉर्निंग रिवर्सल रेजिस्टेंस ${eor.toFixed(0)} के पास पहुँच गया है। पुट साइड ट्रेड एक्टिव है`;
            playHindiMP3(voiceText, `PROX_920_EOR_${Math.round(eor)}`);
        }
    }

    function render920ChartLines(data) {
        if (!candlestickSeries || !data || !data.strategy_920) return;
        clearChartPriceLines();

        const s = data.strategy_920;

        // 1. 9:20 R2 (Upper Resistance / R+1) - Orange/Red Dotted Line
        const r2Val = s.r2_920 || (s.eor_920 ? s.eor_920 + 50 : 0);
        if (r2Val > 0) {
            const line = candlestickSeries.createPriceLine({
                price: r2Val,
                color: '#f97316',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: `9:20 R2 / Upper Res: ${r2Val.toFixed(2)}`,
            });
            activePriceLines.push(line);
        }

        // 2. 9:20 R1 (Primary Resistance / EOR) - Solid Red Line
        const r1Val = s.r1_920 || s.eor_920;
        if (r1Val > 0) {
            const line = candlestickSeries.createPriceLine({
                price: r1Val,
                color: '#ef4444',
                lineWidth: 3,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `9:20 R1 (EOR): ${r1Val.toFixed(2)}`,
            });
            activePriceLines.push(line);
        }

        // 3. 9:20 Mid-Diversion (Center Target Line) - Dotted Yellow/White Line
        const midVal = s.mid_920 || (r1Val && s.s1_920 ? (r1Val + s.s1_920) / 2 : 0);
        if (midVal > 0) {
            const line = candlestickSeries.createPriceLine({
                price: midVal,
                color: '#eab308',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: `9:20 Mid-Pivot / Target: ${midVal.toFixed(2)}`,
            });
            activePriceLines.push(line);
        }

        // 4. 9:20 S1 (Primary Support / EOS) - Solid Green Line
        const s1Val = s.s1_920 || s.eos_920;
        if (s1Val > 0) {
            const line = candlestickSeries.createPriceLine({
                price: s1Val,
                color: '#22c55e',
                lineWidth: 3,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `9:20 S1 (EOS): ${s1Val.toFixed(2)}`,
            });
            activePriceLines.push(line);
        }

        // 5. 9:20 S2 (Lower Support / S-1) - Cyan/Green Dotted Line
        const s2Val = s.s2_920 || (s.eos_920 ? s.eos_920 - 50 : 0);
        if (s2Val > 0) {
            const line = candlestickSeries.createPriceLine({
                price: s2Val,
                color: '#06b6d4',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: `9:20 S2 / Deep Support: ${s2Val.toFixed(2)}`,
            });
            activePriceLines.push(line);
        }

        // 6. 9:20 Entry & SL lines (if trade active)
        if (s.directive_action && s.directive_action.includes("BUY")) {
            if (s.entry_point > 0 && s.entry_point !== s1Val && s.entry_point !== r1Val) {
                const line = candlestickSeries.createPriceLine({
                    price: s.entry_point,
                    color: '#3b82f6',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: `9:20 Entry: ${s.entry_point.toFixed(2)}`,
                });
                activePriceLines.push(line);
            }
            if (s.sl_point > 0) {
                const line = candlestickSeries.createPriceLine({
                    price: s.sl_point,
                    color: '#ec4899',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.Dashed,
                    axisLabelVisible: true,
                    title: `9:20 SL: ${s.sl_point.toFixed(2)}`,
                });
                activePriceLines.push(line);
            }
        }
    }


    // ==========================================
    // TRADINGVIEW LIGHTWEIGHT CHARTS ENGINE (IST FIX)
    // ==========================================
    function initLightweightChart() {
        const container = document.getElementById("chart-container");
        if (!container || chart) return;

        chart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: container.clientHeight || 520,
            layout: {
                background: { type: 'solid', color: '#0f172a' },
                textColor: '#94a3b8',
                fontSize: 12,
            },
            grid: {
                vertLines: { color: '#1e293b' },
                horzLines: { color: '#1e293b' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: '#334155',
                autoScale: true,
            },
            timeScale: {
                borderColor: '#334155',
                timeVisible: true,
                secondsVisible: false,
                tickMarkFormatter: (time, tickMarkType, locale) => {
                    const date = new Date(time * 1000);
                    return date.toLocaleTimeString('en-IN', {
                        timeZone: 'Asia/Kolkata',
                        hour: '2-digit',
                        minute: '2-digit',
                        hour12: true
                    });
                },
            },
            localization: {
                timeFormatter: (timestamp) => {
                    const date = new Date(timestamp * 1000);
                    return date.toLocaleTimeString('en-IN', {
                        timeZone: 'Asia/Kolkata',
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        hour12: true
                    });
                }
            }
        });

        candlestickSeries = chart.addCandlestickSeries({
            upColor: '#22c55e',
            downColor: '#ef4444',
            borderVisible: false,
            wickUpColor: '#22c55e',
            wickDownColor: '#ef4444',
        });

        resizeChart();
    }

    function resizeChart() {
        const container = document.getElementById("chart-container");
        if (chart && container) {
            chart.applyOptions({
                width: container.clientWidth,
                height: container.clientHeight || 520
            });
        }
    }
    window.addEventListener("resize", resizeChart);

    function setDashboardView(mode) {
        activeViewMode = mode;
        const chartCol = document.getElementById("chart-view-container");
        const chainCol = document.getElementById("option-chain-view-container");
        const btnChart = document.getElementById("btn-view-chart");
        const btnChain = document.getElementById("btn-view-chain");
        const btnSplit = document.getElementById("btn-view-split");

        if (!chartCol || !chainCol) return;

        btnChart.classList.remove("active");
        btnChain.classList.remove("active");
        btnSplit.classList.remove("active");

        if (mode === "chart") {
            btnChart.classList.add("active");
            chartCol.classList.remove("d-none", "col-xl-6");
            chartCol.classList.add("col-12");
            chainCol.classList.add("d-none");
        } else if (mode === "chain") {
            btnChain.classList.add("active");
            chainCol.classList.remove("d-none", "col-xl-6");
            chainCol.classList.add("col-12");
            chartCol.classList.add("d-none");
        } else {
            btnSplit.classList.add("active");
            chartCol.classList.remove("d-none", "col-12");
            chartCol.classList.add("col-12", "col-xl-6");
            chainCol.classList.remove("d-none", "col-12");
            chainCol.classList.add("col-12", "col-xl-6");
        }

        setTimeout(() => {
            resizeChart();
        }, 120);
    }

    async function loadChartCandles() {
        const inst = document.getElementById("instrument-select").value;
        const tag = document.getElementById("chart-instrument-tag");
        if (tag) tag.innerText = inst.split("|")[1] || inst;

        try {
            const res = await fetch(`/api/chart-candles?instrument=${encodeURIComponent(inst)}&limit=100`);
            const data = await res.json();
            if (data.status === "success" && data.candles && data.candles.length > 0) {
                candlestickSeries.setData(data.candles);
                currentCandle = { ...data.candles[data.candles.length - 1] };
            } else {
                seedInitialCandles();
            }
        } catch (err) {
            console.warn("Failed to load historical candles:", err);
            seedInitialCandles();
        }
    }

    function seedInitialCandles() {
        if (!candlestickSeries) return;
        const now = Math.floor(Date.now() / 1000);
        let baseSpot = 23000;
        if (lastDashboardData && lastDashboardData.meta && lastDashboardData.meta.spot_price) {
            baseSpot = lastDashboardData.meta.spot_price;
        }
        
        const seeds = [];
        let price = baseSpot - 20;
        for (let i = 30; i >= 0; i--) {
            const t = now - (i * 60);
            const delta = (Math.random() - 0.48) * 12;
            const open = price;
            const close = open + delta;
            const high = Math.max(open, close) + Math.random() * 4;
            const low = Math.min(open, close) - Math.random() * 4;
            price = close;
            seeds.push({ time: t, open: Number(open.toFixed(2)), high: Number(high.toFixed(2)), low: Number(low.toFixed(2)), close: Number(close.toFixed(2)) });
        }
        candlestickSeries.setData(seeds);
        currentCandle = { ...seeds[seeds.length - 1] };
    }

    function updateChartCandle(spot, timestampStr) {
        if (!candlestickSeries || !spot || spot <= 0) return;

        let dt = new Date();
        if (timestampStr) {
            dt = new Date(timestampStr.replace(' ', 'T') + '+05:30');
        }
        dt.setSeconds(0);
        dt.setMilliseconds(0);
        const timeUnix = Math.floor(dt.getTime() / 1000);

        if (!currentCandle || currentCandle.time !== timeUnix) {
            currentCandle = {
                time: timeUnix,
                open: spot,
                high: spot,
                low: spot,
                close: spot
            };
        } else {
            currentCandle.high = Math.max(currentCandle.high, spot);
            currentCandle.low = Math.min(currentCandle.low, spot);
            currentCandle.close = spot;
        }

        try {
            candlestickSeries.update(currentCandle);
        } catch (e) {
            console.warn("Candle update warning:", e);
        }

        const tickEl = document.getElementById("chart-last-tick");
        if (tickEl) tickEl.innerText = `Spot Tick: ${spot.toFixed(2)} @ ${dt.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata', hour12: true })}`;
    }

    function clearChartPriceLines() {
        if (candlestickSeries && activePriceLines.length > 0) {
            activePriceLines.forEach(line => {
                try {
                    candlestickSeries.removePriceLine(line);
                } catch (e) {}
            });
            activePriceLines = [];
        }
    }

    function toggleChartLines() {
        if (currentStrategyMode === "920" && data920Cache) {
            render920ChartLines(data920Cache);
        } else if (lastDashboardData) {
            renderChartPriceLines(lastDashboardData);
        }
    }

    function renderChartPriceLines(data) {
        if (!candlestickSeries || !data || !data.analysis) return;
        clearChartPriceLines();

        const analysis = data.analysis;
        const meta = data.meta || {};
        const records = data.records || [];

        const showEosEor = document.getElementById("chk-eos-eor")?.checked;
        const showUrUs = document.getElementById("chk-ur-us")?.checked;
        const showDivs = document.getElementById("chk-diversions")?.checked;
        const showTargetSl = document.getElementById("chk-target-sl")?.checked;

        // 1. EOS Line (Solid Green)
        if (showEosEor && analysis.eos && analysis.eos.eos_price > 0) {
            const eosVal = Number(analysis.eos.eos_price.toFixed(2));
            const line = candlestickSeries.createPriceLine({
                price: eosVal,
                color: '#22c55e',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `EOS (Support): ${eosVal}`,
            });
            activePriceLines.push(line);
        }

        // 2. EOR Line (Solid Red)
        if (showEosEor && analysis.eor && analysis.eor.eor_price > 0) {
            const eorVal = Number(analysis.eor.eor_price.toFixed(2));
            const line = candlestickSeries.createPriceLine({
                price: eorVal,
                color: '#ef4444',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Solid,
                axisLabelVisible: true,
                title: `EOR (Resist): ${eorVal}`,
            });
            activePriceLines.push(line);
        }

        // 3. UR Line (Dotted Yellow/Orange)
        if (showUrUs && analysis.ur && analysis.ur.ur_price > 0) {
            const urVal = Number(analysis.ur.ur_price.toFixed(2));
            const line = candlestickSeries.createPriceLine({
                price: urVal,
                color: '#f59e0b',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: `UR (Upper Res): ${urVal}`,
            });
            activePriceLines.push(line);
        }

        // 4. US Line (Dotted Cyan/Blue)
        if (showUrUs && analysis.us && analysis.us.us_price > 0) {
            const usVal = Number(analysis.us.us_price.toFixed(2));
            const line = candlestickSeries.createPriceLine({
                price: usVal,
                color: '#06b6d4',
                lineWidth: 2,
                lineStyle: LightweightCharts.LineStyle.Dotted,
                axisLabelVisible: true,
                title: `US (Ult Supp): ${usVal}`,
            });
            activePriceLines.push(line);
        }

        // 5. Key Intermediate Diversions (Thin Dotted Grey)
        if (showDivs && records.length > 0) {
            const spot = meta.spot_price || 0;
            records.sort((a,b) => a.strike_price - b.strike_price);
            const atmStrike = records.reduce((prev, curr) => Math.abs(curr.strike_price - spot) < Math.abs(prev.strike_price - spot) ? curr : prev).strike_price;
            const atmIdx = records.findIndex(r => r.strike_price === atmStrike);
            const nearStrikes = records.slice(Math.max(0, atmIdx - 1), Math.min(records.length, atmIdx + 2));
            
            nearStrikes.forEach(r => {
                if (r.call_diversion && r.call_diversion > 0) {
                    const cDiv = Number(r.call_diversion.toFixed(2));
                    const line = candlestickSeries.createPriceLine({
                        price: cDiv,
                        color: '#9ca3af',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: `Call Div ${r.strike_price}: ${cDiv}`,
                    });
                    activePriceLines.push(line);
                }
                if (r.put_diversion && r.put_diversion > 0) {
                    const pDiv = Number(r.put_diversion.toFixed(2));
                    const line = candlestickSeries.createPriceLine({
                        price: pDiv,
                        color: '#64748b',
                        lineWidth: 1,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: `Put Div ${r.strike_price}: ${pDiv}`,
                    });
                    activePriceLines.push(line);
                }
            });
        }

        // 6. Option Buyer Target / SL Projections
        if (showTargetSl && analysis.buyer_directive) {
            const directive = analysis.buyer_directive;
            const extractNum = (str) => {
                if (!str) return null;
                const m = str.match(/\\d+(\\.\\d+)?/);
                return m ? parseFloat(m[0]) : null;
            };

            const entryVal = extractNum(directive.entry);
            const exitVal = extractNum(directive.exit);
            const slVal = extractNum(directive.sl);

            if (entryVal && entryVal > 0) {
                const line = candlestickSeries.createPriceLine({
                    price: entryVal,
                    color: '#3b82f6',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.LargeDashed,
                    axisLabelVisible: true,
                    title: `Entry: ${entryVal}`,
                });
                activePriceLines.push(line);
            }

            if (exitVal && exitVal > 0) {
                const line = candlestickSeries.createPriceLine({
                    price: exitVal,
                    color: '#10b981',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.LargeDashed,
                    axisLabelVisible: true,
                    title: `Target: ${exitVal}`,
                });
                activePriceLines.push(line);
            }

            if (slVal && slVal > 0) {
                const line = candlestickSeries.createPriceLine({
                    price: slVal,
                    color: '#f97316',
                    lineWidth: 2,
                    lineStyle: LightweightCharts.LineStyle.LargeDashed,
                    axisLabelVisible: true,
                    title: `SL: ${slVal}`,
                });
                activePriceLines.push(line);
            }
        }
    }


    // ==========================================
    // DATA FETCHING & DASHBOARD RENDERING
    // ==========================================
    async function loadExpiries() {
        const inst = document.getElementById("instrument-select").value;
        try {
            const res = await fetch(`/api/expiries?instrument=${encodeURIComponent(inst)}`);
            const data = await res.json();
            if (data.status === "success") {
                const select = document.getElementById("expiry-select");
                select.innerHTML = '<option value="">Auto Nearest</option>';
                data.expiries.forEach(exp => {
                    select.innerHTML += `<option value="${exp}">${exp}</option>`;
                });
            }
        } catch (err) {
            console.error("Failed to load expiries:", err);
        }
    }

    async function loadTimeTravelSnapshots() {
        try {
            const res = await fetch('/api/historical-snapshots?limit=50');
            const data = await res.json();
            if (data.status === "success") {
                const select = document.getElementById("time-travel-select");
                select.innerHTML = '<option value="LIVE">🔴 LIVE REAL-TIME FEED</option>';
                data.snapshots.forEach(s => {
                    select.innerHTML += `<option value="${s.snapshot_id}">⏱️ ${s.timestamp} | ${s.MARKET_STATE || 'Snapshot'}</option>`;
                });
            }
        } catch (err) {
            console.error("Failed to load historical snapshots:", err);
        }
    }

    async function fetchOptionChainData() {
        if (isTimeTravelMode) return;
        const inst = document.getElementById("instrument-select").value;
        const exp = document.getElementById("expiry-select").value;
        let url = `/api/option-chain?instrument=${encodeURIComponent(inst)}`;
        if (exp) url += `&expiry=${encodeURIComponent(exp)}`;

        try {
            const res = await fetch(url);
            const data = await res.json();
            if (data.status === "success") {
                lastDashboardData = data;
                renderDashboard(data);
                handleAlerts(data.alerts);
                
                if (currentStrategyMode === "live" && data.meta && data.meta.spot_price) {
                    updateChartCandle(data.meta.spot_price, data.meta.timestamp);
                    renderChartPriceLines(data);
                }

                startTimer();
                await loadTimeTravelSnapshots();
            }
        } catch (err) {
            console.error("Error fetching option chain:", err);
        }
    }

    async function loadHistoricalSnapshot(snapshotId) {
        if (snapshotId === "LIVE") {
            isTimeTravelMode = false;
            document.getElementById("mode-badge").innerText = "LIVE MODE";
            document.getElementById("mode-badge").className = "badge bg-success";
            if (currentStrategyMode === "920") fetch920StrategyData();
            else fetchOptionChainData();
            startSpotPriceTracker();
            return;
        }

        isTimeTravelMode = true;
        clearInterval(countdownInterval);
        clearInterval(spotTrackerInterval);
        document.getElementById("mode-badge").innerText = "TIME-TRAVEL MODE";
        document.getElementById("mode-badge").className = "badge bg-warning text-dark";

        try {
            const res = await fetch(`/api/historical-snapshot-detail?snapshot_id=${encodeURIComponent(snapshotId)}`);
            const data = await res.json();
            if (data.status === "success") {
                lastDashboardData = data;
                renderDashboard(data);
                if (data.meta && data.meta.spot_price) {
                    updateChartCandle(data.meta.spot_price, data.meta.timestamp);
                    if (currentStrategyMode === "live") renderChartPriceLines(data);
                }
            }
        } catch (err) {
            console.error("Error loading historical snapshot:", err);
        }
    }

    function handleAlerts(alerts) {
        if (!alerts || alerts.length === 0 || isTimeTravelMode) return;
        alerts.forEach(alert => {
            triggerToastAlert(alert.title, alert.message, alert.severity);
            if (alert.voice_text) {
                playHindiMP3(alert.voice_text, alert.alert_key);
            }
        });
    }

    function renderDashboard(data) {
        const meta = data.meta;
        const analysis = data.analysis;
        const records = data.records;

        document.getElementById("last-updated").innerText = (isTimeTravelMode ? "Historical: " : "Updated: ") + meta.timestamp;

        // Render Summary
        const coa = analysis.coa_scenario;
        document.getElementById("badge-scenario-num").innerText = "Scenario " + coa.scenario;
        document.getElementById("scenario-name").innerText = coa.name;
        document.getElementById("scenario-state").innerText = "State: " + coa.market_state;
        document.getElementById("scenario-action").innerHTML = `<i class="fa-solid fa-compass me-2"></i><strong>Action:</strong> ${coa.trading_action}`;

        // Render COA 2.0 Confirmation & IV Trap Filter
        const coa2 = analysis.coa2_confirmation || {};
        const coa2Badge = document.getElementById("coa2-badge");
        if (coa2Badge) {
            coa2Badge.innerText = coa2.badge || "COA 2.0: Checking IV";
            coa2Badge.className = `badge ${coa2.badge_class || 'bg-secondary'}`;
        }
        const coa2Summary = document.getElementById("coa2-summary");
        if (coa2Summary) {
            coa2Summary.innerText = coa2.summary || "Comparative IV analysis active.";
        }

        // Render Option Buyer Directives (Strike, Entry, Exit, SL)
        const directive = analysis.buyer_directive || {};
        const buyerBadge = document.getElementById("buyer-badge");
        if (buyerBadge) {
            buyerBadge.innerText = directive.badge || "NO TRADE ZONE";
            buyerBadge.className = `badge ${directive.badge_class || 'bg-danger text-white'}`;
        }
        const buyerStrike = document.getElementById("buyer-strike");
        if (buyerStrike) buyerStrike.innerText = directive.strike || "-";
        const buyerEntry = document.getElementById("buyer-entry");
        if (buyerEntry) buyerEntry.innerText = directive.entry || "-";
        const buyerExit = document.getElementById("buyer-exit");
        if (buyerExit) buyerExit.innerText = directive.exit || "-";
        const buyerSl = document.getElementById("buyer-sl");
        if (buyerSl) buyerSl.innerText = directive.sl || "-";

        // State of Confusion Alert Banner
        const socBanner = document.getElementById("soc-alert-banner");
        if (analysis.soc_detected) {
            socBanner.classList.remove("d-none");
            socBanner.classList.add("d-flex");
            document.getElementById("soc-alert-text").innerText = "⚠️ " + (analysis.soc_message || "STATE OF CONFUSION DETECTED - NO TRADE ZONE!");
        } else {
            socBanner.classList.remove("d-flex");
            socBanner.classList.add("d-none");
        }

        // Support & Resistance & Shifting Velocity
        const resInfo = analysis.resistance;
        const suppInfo = analysis.support;

        document.getElementById("res-strike").innerText = resInfo.primary_strike.toLocaleString();
        document.getElementById("res-status-badge").innerText = resInfo.overall_status;
        document.getElementById("res-status-badge").className = `badge bg-${resInfo.overall_status === 'STRONG' ? 'danger' : 'warning'}`;
        document.getElementById("res-details").innerText = `Vol: ${resInfo.vol_status} (${resInfo.vol_ratio_pct.toFixed(1)}%) | OI: ${resInfo.oi_status} (${resInfo.oi_ratio_pct.toFixed(1)}%)`;
        
        const resVel = document.getElementById("res-velocity");
        if (resVel && resInfo.shifting_velocity) {
            resVel.innerText = resInfo.shifting_velocity.display_text;
            resVel.className = `small mt-1 badge ${resInfo.shifting_velocity.badge_class}`;
        }

        document.getElementById("supp-strike").innerText = suppInfo.primary_strike.toLocaleString();
        document.getElementById("supp-status-badge").innerText = suppInfo.overall_status;
        document.getElementById("supp-status-badge").className = `badge bg-${suppInfo.overall_status === 'STRONG' ? 'success' : 'warning'}`;
        document.getElementById("supp-details").innerText = `Vol: ${suppInfo.vol_status} (${suppInfo.vol_ratio_pct.toFixed(1)}%) | OI: ${suppInfo.oi_status} (${suppInfo.oi_ratio_pct.toFixed(1)}%)`;
        
        const suppVel = document.getElementById("supp-velocity");
        if (suppVel && suppInfo.shifting_velocity) {
            suppVel.innerText = suppInfo.shifting_velocity.display_text;
            suppVel.className = `small mt-1 badge ${suppInfo.shifting_velocity.badge_class}`;
        }

        // Key Levels & Black-Scholes Theoretical Diversions
        const spot = meta.spot_price;
        document.getElementById("spot-price").innerText = spot.toFixed(2);
        
        // Find ATM Strike
        let atmStrike = 0;
        if (records.length > 0) {
            records.sort((a,b) => a.strike_price - b.strike_price);
            atmStrike = records.reduce((prev, curr) => Math.abs(curr.strike_price - spot) < Math.abs(prev.strike_price - spot) ? curr : prev).strike_price;
        }
        document.getElementById("atm-strike").innerText = atmStrike.toLocaleString();

        document.getElementById("eos-price").innerText = analysis.eos.eos_price.toFixed(2);
        const eosBs = document.getElementById("eos-bs");
        if (eosBs) eosBs.innerText = "BS IV: " + (analysis.eos.eos_bs_price ? analysis.eos.eos_bs_price.toFixed(2) : analysis.eos.eos_price.toFixed(2));

        document.getElementById("eor-price").innerText = analysis.eor.eor_price.toFixed(2);
        const eorBs = document.getElementById("eor-bs");
        if (eorBs) eorBs.innerText = "BS IV: " + (analysis.eor.eor_bs_price ? analysis.eor.eor_bs_price.toFixed(2) : analysis.eor.eor_price.toFixed(2));

        document.getElementById("ur-price").innerText = (analysis.ur ? analysis.ur.ur_price : analysis.eor.eor_price).toFixed(2);
        document.getElementById("us-price").innerText = (analysis.us ? analysis.us.us_price : analysis.eos.eos_price).toFixed(2);

        const totalCallOI = records.reduce((sum, r) => sum + r.call_oi, 0);
        const totalPutOI = records.reduce((sum, r) => sum + r.put_oi, 0);
        const pcr = totalCallOI > 0 ? (totalPutOI / totalCallOI).toFixed(2) : "0.0";
        document.getElementById("pcr-val").innerText = pcr;

        // Filter +- 12 strikes around ATM
        let displayedRecords = records;
        if (spot > 0) {
            const atmIdx = records.findIndex(r => r.strike_price === atmStrike);
            if (atmIdx >= 0) {
                const startIdx = Math.max(0, atmIdx - 12);
                const endIdx = Math.min(records.length, atmIdx + 13);
                displayedRecords = records.slice(startIdx, endIdx);
            }
        }

        // Max values for relative heatmap gradients
        const maxCallVol = Math.max(...displayedRecords.map(r => r.call_volume), 1);
        const maxCallOI = Math.max(...displayedRecords.map(r => r.call_oi), 1);
        const maxPutVol = Math.max(...displayedRecords.map(r => r.put_volume), 1);
        const maxPutOI = Math.max(...displayedRecords.map(r => r.put_oi), 1);

        // Render Table Body
        const tbody = document.getElementById("table-body");
        tbody.innerHTML = "";

        let spotInserted = false;

        displayedRecords.forEach((r, idx) => {
            const strike = r.strike_price;
            const nextR = displayedRecords[idx + 1];

            // Floating Live Spot Ticker Row between boundary strikes
            if (!spotInserted && spot > 0 && nextR && strike <= spot && spot < nextR.strike_price) {
                const spotTr = document.createElement("tr");
                spotTr.className = "table-info border-3 border-info text-center fw-bold fs-6";
                spotTr.style.background = "linear-gradient(90deg, #0f172a 0%, #1e293b 50%, #0f172a 100%)";
                spotTr.innerHTML = `
                    <td colspan="11" class="py-2 text-info" style="border-top: 2px solid #0dcaf0; border-bottom: 2px solid #0dcaf0;">
                        <i class="fa-solid fa-crosshairs text-warning me-2 fs-5"></i>
                        <span class="badge bg-info text-dark me-2 font-monospace fs-6">LIVE SPOT TICKER</span>
                        <strong id="floating-spot-ticker-text" class="fs-5 text-warning tracking-wide">─── 🎯 SPOT PRICE: ${spot.toFixed(2)} ▲ ───</strong>
                    </td>
                `;
                tbody.appendChild(spotTr);
                spotInserted = true;
            }

            const isATM = (strike === atmStrike);
            const isRes = (strike === resInfo.primary_strike);
            const isSupp = (strike === suppInfo.primary_strike);
            const isWTT_WTB = (isRes && resInfo.overall_status !== "STRONG") || (isSupp && suppInfo.overall_status !== "STRONG");

            const isCallITM = (strike < spot);
            const isPutITM = (strike > spot);

            let rowClass = "";
            let strikeBadge = "";
            if (isATM) {
                rowClass = "bg-atm";
                strikeBadge = '<span class="badge badge-atm-tag ms-1">ATM</span>';
            } else if (isRes) {
                rowClass = "bg-resistance";
                strikeBadge = '<span class="badge badge-res ms-1">RES</span>';
            } else if (isSupp) {
                rowClass = "bg-support";
                strikeBadge = '<span class="badge badge-supp ms-1">SUPP</span>';
            }

            if (isWTT_WTB) {
                rowClass += " bg-wtt-wtb";
            }

            // Cell Highlight Classes
            const isCallHighestVol = (r.call_volume === resInfo.highest_vol_val && resInfo.highest_vol_val > 0);
            const isCallHighestOI = (r.call_oi === resInfo.highest_oi_val && resInfo.highest_oi_val > 0);
            const isCall2ndWTT_WTB = ((strike === resInfo.second_vol_strike && resInfo.vol_ratio_pct >= 75) || (strike === resInfo.second_oi_strike && resInfo.oi_ratio_pct >= 75));

            let callVolClass = isCallHighestVol ? "cell-highest-res" : (isCall2ndWTT_WTB ? "cell-second-wtt-wtb" : "");
            let callOIClass = isCallHighestOI ? "cell-highest-res" : (isCall2ndWTT_WTB ? "cell-second-wtt-wtb" : "");

            const isPutHighestVol = (r.put_volume === suppInfo.highest_vol_val && suppInfo.highest_vol_val > 0);
            const isPutHighestOI = (r.put_oi === suppInfo.highest_oi_val && suppInfo.highest_oi_val > 0);
            const isPut2ndWTT_WTB = ((strike === suppInfo.second_vol_strike && suppInfo.vol_ratio_pct >= 75) || (strike === suppInfo.second_oi_strike && suppInfo.oi_ratio_pct >= 75));

            let putVolClass = isPutHighestVol ? "cell-highest-supp" : (isPut2ndWTT_WTB ? "cell-second-wtt-wtb" : "");
            let putOIClass = isPutHighestOI ? "cell-highest-supp" : (isPut2ndWTT_WTB ? "cell-second-wtt-wtb" : "");

            // Heatmap Gradient Percentages
            const callVolPct = Math.min(100, Math.round((r.call_volume / maxCallVol) * 100));
            const callOIPct = Math.min(100, Math.round((r.call_oi / maxCallOI) * 100));
            const putVolPct = Math.min(100, Math.round((r.put_volume / maxPutVol) * 100));
            const putOIPct = Math.min(100, Math.round((r.put_oi / maxPutOI) * 100));

            const callVolStyle = isCallHighestVol || isCall2ndWTT_WTB ? "" : `background: linear-gradient(90deg, rgba(220, 53, 69, 0.18) ${callVolPct}%, transparent ${callVolPct}%);`;
            const callOIStyle = isCallHighestOI || isCall2ndWTT_WTB ? "" : `background: linear-gradient(90deg, rgba(220, 53, 69, 0.18) ${callOIPct}%, transparent ${callOIPct}%);`;
            const putVolStyle = isPutHighestVol || isPut2ndWTT_WTB ? "" : `background: linear-gradient(270deg, rgba(25, 135, 84, 0.18) ${putVolPct}%, transparent ${putVolPct}%);`;
            const putOIStyle = isPutHighestOI || isPut2ndWTT_WTB ? "" : `background: linear-gradient(270deg, rgba(25, 135, 84, 0.18) ${putOIPct}%, transparent ${putOIPct}%);`;

            const cDivToolTip = `Calculated via Black-Scholes IV @ ${meta.timestamp} | Simple Div: ${r.call_diversion.toFixed(2)} | BS Spot Reversal: ${r.call_bs_reversal || '-'}`;
            const pDivToolTip = `Calculated via Black-Scholes IV @ ${meta.timestamp} | Simple Div: ${r.put_diversion.toFixed(2)} | BS Spot Reversal: ${r.put_bs_reversal || '-'}`;

            const tr = document.createElement("tr");
            tr.className = rowClass;
            tr.innerHTML = `
                <td class="text-end fw-semibold ${callOIClass} ${isCallITM ? 'bg-itm-tint' : ''}" style="${callOIStyle}">${formatNumber(r.call_oi)}</td>
                <td class="text-end ${r.call_oi_change >= 0 ? 'text-success' : 'text-danger'} ${isCallITM ? 'bg-itm-tint' : ''}">${r.call_oi_change >= 0 ? '+' : ''}${formatNumber(r.call_oi_change)}</td>
                <td class="text-end ${callVolClass} ${isCallITM ? 'bg-itm-tint' : ''}" style="${callVolStyle}">${formatNumber(r.call_volume)}</td>
                
                <td class="text-end text-warning fw-bold ${isCallITM ? 'bg-itm-tint' : ''}" style="cursor: pointer;" data-bs-toggle="tooltip" data-bs-placement="top" title="${cDivToolTip}" onclick="alert('Calculated via Black-Scholes IV @ ${meta.timestamp}\\nCall Diversion: ${r.call_diversion.toFixed(2)}\\nBS Reversal Spot: ${r.call_bs_reversal || '-'}')">
                    ${r.call_diversion.toFixed(1)} <i class="fa-solid fa-circle-info ms-1 text-info opacity-75" style="font-size:0.75rem;"></i>
                </td>
                
                <td class="text-end fw-bold text-info ${isCallITM ? 'bg-itm-tint' : ''}">${r.call_ltp.toFixed(2)}</td>
                
                <td class="text-center fw-bold fs-6" style="background-color: #1e293b;">
                    ${strike.toLocaleString()} ${strikeBadge}
                </td>
                
                <td class="text-start fw-bold text-info ${isPutITM ? 'bg-itm-tint' : ''}">${r.put_ltp.toFixed(2)}</td>
                
                <td class="text-start text-warning fw-bold ${isPutITM ? 'bg-itm-tint' : ''}" style="cursor: pointer;" data-bs-toggle="tooltip" data-bs-placement="top" title="${pDivToolTip}" onclick="alert('Calculated via Black-Scholes IV @ ${meta.timestamp}\\nPut Diversion: ${r.put_diversion.toFixed(2)}\\nBS Reversal Spot: ${r.put_bs_reversal || '-'}')">
                    <i class="fa-solid fa-circle-info me-1 text-info opacity-75" style="font-size:0.75rem;"></i> ${r.put_diversion.toFixed(1)}
                </td>
                
                <td class="text-start ${putVolClass} ${isPutITM ? 'bg-itm-tint' : ''}" style="${putVolStyle}">${formatNumber(r.put_volume)}</td>
                <td class="text-start ${r.put_oi_change >= 0 ? 'text-success' : 'text-danger'} ${isPutITM ? 'bg-itm-tint' : ''}">${r.put_oi_change >= 0 ? '+' : ''}${formatNumber(r.put_oi_change)}</td>
                <td class="text-start fw-semibold ${putOIClass} ${isPutITM ? 'bg-itm-tint' : ''}" style="${putOIStyle}">${formatNumber(r.put_oi)}</td>
            `;
            tbody.appendChild(tr);
        });

        // Re-initialize Bootstrap Tooltips
        const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
        tooltipTriggerList.forEach(el => new bootstrap.Tooltip(el));
    }

    // Event Listeners
    document.getElementById("btn-test-voice").addEventListener("click", testHindiVoice);

    document.getElementById("btn-voice-toggle").addEventListener("click", () => {
        voiceAlertsEnabled = !voiceAlertsEnabled;
        const btn = document.getElementById("btn-voice-toggle");
        if (voiceAlertsEnabled) {
            btn.className = "btn btn-outline-warning btn-sm";
            btn.innerHTML = '<i class="fa-solid fa-volume-high me-1"></i> 🔊 Event Voice: Active';
            playHindiMP3("स्मार्ट हिंदी वॉइस अलर्ट चालू हो गए हैं", "VOICE_TOGGLE_ON_" + Date.now());
        } else {
            btn.className = "btn btn-outline-secondary btn-sm";
            btn.innerHTML = '<i class="fa-solid fa-volume-xmark me-1"></i> 🔇 Event Voice: Disabled';
        }
    });

    document.getElementById("btn-refresh").addEventListener("click", () => {
        isTimeTravelMode = false;
        document.getElementById("time-travel-select").value = "LIVE";
        document.getElementById("mode-badge").innerText = "LIVE MODE";
        document.getElementById("mode-badge").className = "badge bg-success";
        if (currentStrategyMode === "920") fetch920StrategyData();
        else fetchOptionChainData();
    });

    document.getElementById("btn-reset-live").addEventListener("click", () => {
        isTimeTravelMode = false;
        document.getElementById("time-travel-select").value = "LIVE";
        document.getElementById("mode-badge").innerText = "LIVE MODE";
        document.getElementById("mode-badge").className = "badge bg-success";
        if (currentStrategyMode === "920") fetch920StrategyData();
        else fetchOptionChainData();
        startSpotPriceTracker();
    });

    document.getElementById("time-travel-select").addEventListener("change", (e) => {
        loadHistoricalSnapshot(e.target.value);
    });

    document.getElementById("instrument-select").addEventListener("change", async () => {
        await loadExpiries();
        await loadChartCandles();
        if (currentStrategyMode === "920") fetch920StrategyData();
        else fetchOptionChainData();
    });
    document.getElementById("expiry-select").addEventListener("change", () => {
        if (currentStrategyMode === "920") fetch920StrategyData();
        else fetchOptionChainData();
    });

    // Initial Load Sequence
    window.addEventListener("DOMContentLoaded", async () => {
        initLightweightChart();
        await loadExpiries();
        await loadChartCandles();
        fetchOptionChainData();
        startSpotPriceTracker();
        await loadTimeTravelSnapshots();
    });
</script>

</body>
</html>
"""
    return HTMLResponse(content=html_content)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    print(f"[INFO] Launching Ashish Goswami LTP Calculator Pro Dashboard on http://localhost:{port}")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=True)
