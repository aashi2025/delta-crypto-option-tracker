import os
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
import requests

# Fix UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Strict Indian Standard Time (IST = UTC+05:30) Definition
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime:
    """Returns current datetime strictly in Indian Standard Time (IST, UTC+5:30)."""
    return datetime.now(timezone.utc).astimezone(IST)

def get_ist_str() -> str:
    """Returns current timestamp string formatted as 'YYYY-MM-DD HH:MM:SS' strictly in IST."""
    return get_ist_now().strftime("%Y-%m-%d %H:%M:%S")

def get_ist_date_str() -> str:
    """Returns current date string formatted as 'YYYY-MM-DD' strictly in IST."""
    return get_ist_now().strftime("%Y-%m-%d")

def unix_to_ist_str(unix_ts: int) -> str:
    """Converts a Unix timestamp (seconds) to a formatted string strictly in IST."""
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


class TradeAuditEngine:
    """
    Automated Trade Audit & Performance Tracker Engine (Dr. Vinay Prakash Tiwari LTP Reversal Setups).
    Features:
    1. Background Level Trigger Engine (EOS Bullish / EOR Bearish within 0.3% buffer range).
    2. SQLite Database Trade Logging (trade_audit_log).
    3. Outcome Evaluation Engine (Target vs SL Candle Evaluation via Delta Exchange Historical OHLC).
    4. Debounce / Cooldown filter to prevent duplicate entries.
    5. Strict IST TimeZone synchronization across all serverless and VPS environments.
    6. Exact Delta Exchange index symbol tracking (.DEXBTUSD for BTC, .DEETHUSD for ETH).
    """

    def __init__(
        self,
        db,
        service,
        calc_engine,
        buffer_pct: float = 0.003, # 0.3% buffer range
        cooldown_minutes: int = 60 # 60 minutes anti-spam debounce
    ):
        self.db = db
        self.service = service
        self.calc_engine = calc_engine
        self.buffer_pct = buffer_pct
        self.cooldown_minutes = cooldown_minutes
        self.running = False
        self.worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start_background_worker(self, interval_seconds: int = 180):
        """Starts non-blocking background daemon worker running every 3-5 minutes (default: 180s)."""
        if self.running:
            return

        self.running = True
        self.worker_thread = threading.Thread(
            target=self._background_loop,
            args=(interval_seconds,),
            daemon=True,
            name="TradeAuditWorker"
        )
        self.worker_thread.start()
        print(f"[INFO] 🚀 TradeAuditEngine background worker started (Interval: {interval_seconds}s)")

    def stop_background_worker(self):
        """Stops background worker gracefully."""
        self.running = False

    def _background_loop(self, interval_seconds: int):
        """Continuous background thread evaluating setups and active trades for BTC and ETH."""
        time.sleep(3)
        while self.running:
            try:
                self.evaluate_and_log_all_assets()
            except Exception as e:
                print(f"[WARNING] TradeAuditEngine worker cycle error: {e}")

            for _ in range(interval_seconds):
                if not self.running:
                    break
                time.sleep(1)

    def evaluate_and_log_all_assets(self, assets: List[str] = ["BTC", "ETH"]):
        """Runs evaluation for all supported crypto assets."""
        with self._lock:
            for asset in assets:
                try:
                    self.check_and_trigger_setups(asset)
                except Exception as e:
                    print(f"[WARNING] Error checking setups for {asset}: {e}")

                try:
                    self.evaluate_active_trades(asset)
                except Exception as e:
                    print(f"[WARNING] Error evaluating active trades for {asset}: {e}")

    def calculate_levels(
        self,
        setup_type: str,
        spot: float,
        eos_price: float,
        eor_price: float,
        supp_strike: float,
        res_strike: float,
        records: List[Dict[str, Any]],
        ur_price: float = 0.0,
        us_price: float = 0.0
    ) -> Tuple[float, float, float]:
        """
        Calculates exact Entry, Target (Next Strike Diversion), and SL according to COA Reversal principles:
        Returns: (entry_level, target_price, sl_price)
        """
        if setup_type == "EOS Bullish Reversal":
            entry_level = round(eos_price, 2)
            
            # Target: Next Strike Diversion above Support Strike
            strikes_above = sorted([r for r in records if r.get("strike_price", 0) > supp_strike], key=lambda x: x["strike_price"])
            target_candidates = []
            for r in strikes_above:
                c_div = r.get("call_diversion", 0.0)
                p_div = r.get("put_diversion", 0.0)
                if c_div > entry_level:
                    target_candidates.append(c_div)
                if p_div > entry_level:
                    target_candidates.append(p_div)

            if target_candidates:
                target_price = round(min(target_candidates), 2)
            elif eor_price > entry_level:
                target_price = round(eor_price, 2)
            else:
                target_price = round(entry_level * 1.01, 2)

            # SL: US level (Ultimate Support) if below EOS, else 0.5% below EOS
            if us_price > 0 and us_price < entry_level:
                sl_price = round(us_price, 2)
            else:
                sl_price = round(entry_level * 0.995, 2)

            # Safeguards
            if sl_price >= entry_level:
                sl_price = round(entry_level * 0.995, 2)
            if target_price <= entry_level:
                target_price = round(entry_level * 1.01, 2)

            return entry_level, target_price, sl_price

        else: # "EOR Bearish Reversal"
            entry_level = round(eor_price, 2)

            # Target: Next Strike Diversion below Resistance Strike
            strikes_below = sorted([r for r in records if r.get("strike_price", 0) < res_strike], key=lambda x: x["strike_price"], reverse=True)
            target_candidates = []
            for r in strikes_below:
                p_div = r.get("put_diversion", 0.0)
                c_div = r.get("call_diversion", 0.0)
                if p_div > 0 and p_div < entry_level:
                    target_candidates.append(p_div)
                if c_div > 0 and c_div < entry_level:
                    target_candidates.append(c_div)

            if target_candidates:
                target_price = round(max(target_candidates), 2)
            elif eos_price > 0 and eos_price < entry_level:
                target_price = round(eos_price, 2)
            else:
                target_price = round(entry_level * 0.99, 2)

            # SL: UR level (Upper Resistance) if above EOR, else 0.5% above EOR
            if ur_price > entry_level:
                sl_price = round(ur_price, 2)
            else:
                sl_price = round(entry_level * 1.005, 2)

            # Safeguards
            if sl_price <= entry_level:
                sl_price = round(entry_level * 1.005, 2)
            if target_price >= entry_level:
                target_price = round(entry_level * 0.99, 2)

            return entry_level, target_price, sl_price

    def is_setup_debounced(self, asset: str, setup_type: str) -> bool:
        """
        Cooldown/Debounce mechanism:
        Returns True if:
        1. An ACTIVE trade already exists for this (asset, setup_type).
        2. The most recent trade for this setup was entered within `cooldown_minutes`.
        """
        active_trades = self.db.get_active_trades(asset=asset)
        if any(t["setup_type"] == setup_type for t in active_trades):
            return True # Duplicate active trade blocked

        last_trade = self.db.get_last_trade_for_setup(asset, setup_type)
        if last_trade:
            ts_str = last_trade.get("timestamp", "")
            try:
                dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                elapsed_min = (get_ist_now() - dt).total_seconds() / 60.0
                if elapsed_min < self.cooldown_minutes:
                    return True # In cooldown period
            except Exception:
                pass

        return False

    def check_and_trigger_setups(self, asset: str = "BTC") -> List[Dict[str, Any]]:
        """
        Background Level Trigger Engine (STRICT IST TIME):
        1. Bullish Setup Trigger:
           - Spot Price at EOS within 0.3% buffer: abs(spot - eos) / eos <= 0.003
           - Support is "Strong" (no downward WTB, no SOC)
        2. Bearish Setup Trigger:
           - Spot Price at EOR within 0.3% buffer: abs(spot - eor) / eor <= 0.003
           - Resistance is "Strong" (no upward WTT, no SOC)
        Logs to SQLite table trade_audit_log immediately if triggered.
        """
        triggered = []
        symbol_prefix = asset.upper()

        records, meta = self.service.fetch_option_chain_snapshot(symbol_prefix=symbol_prefix)
        if not records or not meta:
            return triggered

        spot = float(meta.get("spot_price") or 0.0)
        if spot <= 0:
            return triggered

        analysis = self.calc_engine.analyze(records, spot, meta.get("expiry_date", ""))
        if not analysis:
            return triggered

        eos_info = analysis.get("eos", {})
        eor_info = analysis.get("eor", {})
        supp_info = analysis.get("support", {})
        res_info = analysis.get("resistance", {})
        ur_info = analysis.get("ur", {})
        us_info = analysis.get("us", {})
        soc_detected = analysis.get("soc_detected", False)

        eos_price = float(eos_info.get("eos_price") or 0.0)
        eor_price = float(eor_info.get("eor_price") or 0.0)
        supp_status = supp_info.get("overall_status", "STRONG")
        res_status = res_info.get("overall_status", "STRONG")
        supp_strike = float(supp_info.get("primary_strike") or 0.0)
        res_strike = float(res_info.get("primary_strike") or 0.0)
        ur_price = float(ur_info.get("ur_price") or 0.0)
        us_price = float(us_info.get("us_price") or 0.0)
        records_with_reversals = analysis.get("records_with_reversals", records)

        # STRICT IST Current Time (Fixed for Vercel Serverless)
        now_str = get_ist_str()

        # -------------------------------------------------------------
        # 1. BULLISH SETUP TRIGGER CHECK (EOS Reversal)
        # -------------------------------------------------------------
        if eos_price > 0 and not soc_detected:
            eos_diff_pct = abs(spot - eos_price) / eos_price
            if eos_diff_pct <= self.buffer_pct and supp_status == "STRONG":
                setup_type = "EOS Bullish Reversal"
                if not self.is_setup_debounced(symbol_prefix, setup_type):
                    entry, target, sl = self.calculate_levels(
                        setup_type, spot, eos_price, eor_price, supp_strike, res_strike,
                        records_with_reversals, ur_price, us_price
                    )
                    notes = f"Support Strong @ {supp_strike:,.0f} | EOS: {eos_price:,.2f} | Spot diff: {eos_diff_pct*100:.2f}%"
                    trade_id = self.db.insert_trade_audit(
                        timestamp=now_str,
                        asset=symbol_prefix,
                        setup_type=setup_type,
                        trigger_price=round(spot, 2),
                        entry_level=entry,
                        target_price=target,
                        sl_price=sl,
                        status="ACTIVE",
                        notes=notes
                    )
                    log_item = {
                        "id": trade_id,
                        "timestamp": now_str,
                        "asset": symbol_prefix,
                        "setup_type": setup_type,
                        "trigger_price": spot,
                        "entry_level": entry,
                        "target_price": target,
                        "sl_price": sl,
                        "status": "ACTIVE"
                    }
                    triggered.append(log_item)
                    print(f"[AUDIT TRIGGER] 🟢 Bullish Setup Logged ({now_str} IST): {symbol_prefix} @ {spot:,.2f} (Target: {target:,.2f}, SL: {sl:,.2f})")

        # -------------------------------------------------------------
        # 2. BEARISH SETUP TRIGGER CHECK (EOR Reversal)
        # -------------------------------------------------------------
        if eor_price > 0 and not soc_detected:
            eor_diff_pct = abs(spot - eor_price) / eor_price
            if eor_diff_pct <= self.buffer_pct and res_status == "STRONG":
                setup_type = "EOR Bearish Reversal"
                if not self.is_setup_debounced(symbol_prefix, setup_type):
                    entry, target, sl = self.calculate_levels(
                        setup_type, spot, eos_price, eor_price, supp_strike, res_strike,
                        records_with_reversals, ur_price, us_price
                    )
                    notes = f"Resistance Strong @ {res_strike:,.0f} | EOR: {eor_price:,.2f} | Spot diff: {eor_diff_pct*100:.2f}%"
                    trade_id = self.db.insert_trade_audit(
                        timestamp=now_str,
                        asset=symbol_prefix,
                        setup_type=setup_type,
                        trigger_price=round(spot, 2),
                        entry_level=entry,
                        target_price=target,
                        sl_price=sl,
                        status="ACTIVE",
                        notes=notes
                    )
                    log_item = {
                        "id": trade_id,
                        "timestamp": now_str,
                        "asset": symbol_prefix,
                        "setup_type": setup_type,
                        "trigger_price": spot,
                        "entry_level": entry,
                        "target_price": target,
                        "sl_price": sl,
                        "status": "ACTIVE"
                    }
                    triggered.append(log_item)
                    print(f"[AUDIT TRIGGER] 🔴 Bearish Setup Logged ({now_str} IST): {symbol_prefix} @ {spot:,.2f} (Target: {target:,.2f}, SL: {sl:,.2f})")

        return triggered

    def get_delta_chart_symbol(self, asset: str) -> str:
        """Exact underlying index ticker symbol on Delta Exchange India."""
        asset_u = asset.upper()
        if asset_u == "BTC":
            return ".DEXBTUSD"
        elif asset_u == "ETH":
            return ".DEETHUSD"
        elif asset_u == "SOL":
            return ".DESOLUSD"
        return f".DE{asset_u}USD"

    def fetch_delta_candles(self, asset: str, from_ts: int, to_ts: int, resolution: str = "5") -> List[Dict[str, Any]]:
        """
        Fetches historical OHLC candles from Delta Exchange REST API:
        https://api.delta.exchange/v2/chart/history
        Uses exact underlying index (.DEXBTUSD for BTC, .DEETHUSD for ETH).
        Returns sorted chronological candles list: [{'t', 'o', 'h', 'l', 'c', 'v'}, ...]
        """
        url = f"{self.service.base_url}/v2/chart/history"
        primary_sym = self.get_delta_chart_symbol(asset)
        symbols_to_try = [primary_sym, f"{asset.upper()}USDT", f"{asset.upper()}USD"]

        for sym in symbols_to_try:
            params = {
                "symbol": sym,
                "resolution": resolution,
                "from": from_ts,
                "to": to_ts
            }
            try:
                r = requests.get(url, params=params, headers=self.service._get_headers(), timeout=8)
                if r.status_code == 200:
                    res = r.json().get("result", {})
                    t_arr = res.get("t", [])
                    o_arr = res.get("o", [])
                    h_arr = res.get("h", [])
                    l_arr = res.get("l", [])
                    c_arr = res.get("c", [])
                    v_arr = res.get("v", [])

                    if len(t_arr) > 0 and len(c_arr) > 0:
                        candles = []
                        for i in range(len(t_arr)):
                            candles.append({
                                "t": t_arr[i],
                                "o": float(o_arr[i]),
                                "h": float(h_arr[i]),
                                "l": float(l_arr[i]),
                                "c": float(c_arr[i]),
                                "v": float(v_arr[i]) if i < len(v_arr) else 0.0
                            })
                        # Sort chronologically (oldest to newest)
                        candles.sort(key=lambda x: x["t"])
                        return candles
            except Exception as e:
                print(f"[WARNING] Failed to fetch Delta candles for {asset} with {sym}: {e}")

        return []

    def evaluate_active_trades(self, asset: Optional[str] = None):
        """
        OUTCOME EVALUATION ENGINE:
        Monitors incoming price candles for every ACTIVE trade (strictly in IST):
        - Bullish Trade:
          * High >= Target -> TARGET HIT ✅ (points_captured = target - entry)
          * Low <= SL -> SL HIT ❌ (loss_points = entry - sl)
        - Bearish Trade:
          * Low <= Target -> TARGET HIT ✅ (points_captured = entry - target)
          * High >= SL -> SL HIT ❌ (loss_points = sl - entry)
        """
        active_trades = self.db.get_active_trades(asset=asset)
        if not active_trades:
            return

        now_unix = int(time.time())
        now_str = get_ist_str()

        spot_map = {}

        for trade in active_trades:
            t_id = trade["id"]
            trade_asset = trade["asset"]
            setup_type = trade["setup_type"]
            entry_level = trade["entry_level"]
            target_price = trade["target_price"]
            sl_price = trade["sl_price"]
            ts_str = trade["timestamp"]

            # Parse entry timestamp with IST awareness
            entry_unix = now_unix - 3600
            try:
                dt_entry = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
                entry_unix = int(dt_entry.timestamp())
            except Exception:
                pass

            # Fetch candles from entry time to now
            candles = self.fetch_delta_candles(trade_asset, from_ts=entry_unix - 60, to_ts=now_unix, resolution="5")

            resolved = False

            # 1. Candle Check (Chronological Order)
            for c in candles:
                c_time = c["t"]
                c_high = c["h"]
                c_low = c["l"]

                if c_time < entry_unix:
                    continue

                # Format candle time in exact IST
                c_time_str = unix_to_ist_str(c_time)
                duration_min = round(max(1.0, (c_time - entry_unix) / 60.0), 1)

                if setup_type == "EOS Bullish Reversal":
                    if c_high >= target_price:
                        pts = round(target_price - entry_level, 2)
                        self.db.update_trade_outcome(
                            trade_id=t_id,
                            status="TARGET HIT ✅",
                            exit_time=c_time_str,
                            points_captured=pts,
                            loss_points=0.0,
                            duration_minutes=duration_min,
                            notes=f"Target reached on 5m candle @ High {c_high:,.2f}"
                        )
                        resolved = True
                        print(f"[AUDIT RESOLVED] 🎯 Trade #{t_id} {trade_asset} TARGET HIT ({c_time_str} IST): +{pts:,.2f} pts in {duration_min} min")
                        break
                    elif c_low <= sl_price:
                        loss_pts = round(entry_level - sl_price, 2)
                        self.db.update_trade_outcome(
                            trade_id=t_id,
                            status="SL HIT ❌",
                            exit_time=c_time_str,
                            points_captured=0.0,
                            loss_points=loss_pts,
                            duration_minutes=duration_min,
                            notes=f"SL breached on 5m candle @ Low {c_low:,.2f}"
                        )
                        resolved = True
                        print(f"[AUDIT RESOLVED] ❌ Trade #{t_id} {trade_asset} SL HIT ({c_time_str} IST): -{loss_pts:,.2f} pts in {duration_min} min")
                        break

                elif setup_type == "EOR Bearish Reversal":
                    if c_low <= target_price:
                        pts = round(entry_level - target_price, 2)
                        self.db.update_trade_outcome(
                            trade_id=t_id,
                            status="TARGET HIT ✅",
                            exit_time=c_time_str,
                            points_captured=pts,
                            loss_points=0.0,
                            duration_minutes=duration_min,
                            notes=f"Target reached on 5m candle @ Low {c_low:,.2f}"
                        )
                        resolved = True
                        print(f"[AUDIT RESOLVED] 🎯 Trade #{t_id} {trade_asset} TARGET HIT ({c_time_str} IST): +{pts:,.2f} pts in {duration_min} min")
                        break
                    elif c_high >= sl_price:
                        loss_pts = round(sl_price - entry_level, 2)
                        self.db.update_trade_outcome(
                            trade_id=t_id,
                            status="SL HIT ❌",
                            exit_time=c_time_str,
                            points_captured=0.0,
                            loss_points=loss_pts,
                            duration_minutes=duration_min,
                            notes=f"SL breached on 5m candle @ High {c_high:,.2f}"
                        )
                        resolved = True
                        print(f"[AUDIT RESOLVED] ❌ Trade #{t_id} {trade_asset} SL HIT ({c_time_str} IST): -{loss_pts:,.2f} pts in {duration_min} min")
                        break

            # 2. Live Spot Fallback Check
            if not resolved:
                if trade_asset not in spot_map:
                    try:
                        _, meta = self.service.fetch_option_chain_snapshot(symbol_prefix=trade_asset)
                        spot_map[trade_asset] = float(meta.get("spot_price") or 0.0)
                    except Exception:
                        spot_map[trade_asset] = 0.0

                live_spot = spot_map.get(trade_asset, 0.0)
                if live_spot > 0:
                    duration_min = round(max(1.0, (now_unix - entry_unix) / 60.0), 1)
                    if setup_type == "EOS Bullish Reversal":
                        if live_spot >= target_price:
                            pts = round(target_price - entry_level, 2)
                            self.db.update_trade_outcome(t_id, "TARGET HIT ✅", now_str, pts, 0.0, duration_min, f"Live Spot hit Target: {live_spot:,.2f}")
                        elif live_spot <= sl_price:
                            loss_pts = round(entry_level - sl_price, 2)
                            self.db.update_trade_outcome(t_id, "SL HIT ❌", now_str, 0.0, loss_pts, duration_min, f"Live Spot hit SL: {live_spot:,.2f}")
                    elif setup_type == "EOR Bearish Reversal":
                        if live_spot <= target_price:
                            pts = round(entry_level - target_price, 2)
                            self.db.update_trade_outcome(t_id, "TARGET HIT ✅", now_str, pts, 0.0, duration_min, f"Live Spot hit Target: {live_spot:,.2f}")
                        elif live_spot >= sl_price:
                            loss_pts = round(sl_price - entry_level, 2)
                            self.db.update_trade_outcome(t_id, "SL HIT ❌", now_str, 0.0, loss_pts, duration_min, f"Live Spot hit SL: {live_spot:,.2f}")

    def seed_historical_audit_records_if_empty(self):
        """
        No-op: Does NOT inject mock or fake data.
        Ensures 100% of recorded trades are authentic live-market triggers matching Delta Exchange charts.
        """
        pass

    def get_dashboard_audit_data(self, date_filter: Optional[str] = None, asset: Optional[str] = None) -> Dict[str, Any]:
        """
        Prepares complete response payload for frontend Trade Audit UI:
        - Summary Statistics
        - Available dates for filter dropdown
        - Detailed trade audit log records with clear visual badges
        """
        stats = self.db.get_audit_stats(date_filter=date_filter, asset=asset)
        dates = self.db.get_audit_dates()
        records = self.db.get_trade_audit_log(date_filter=date_filter, asset=asset, limit=200)

        enhanced_trades = []
        for r in records:
            status = r.get("status", "ACTIVE")
            is_target = "TARGET HIT" in status
            is_sl = "SL HIT" in status
            is_active = (status == "ACTIVE")

            if is_target:
                badge_class = "bg-success text-white"
                outcome_text = f"TARGET HIT ✅ (+{r.get('points_captured', 0):,.1f} pts)"
            elif is_sl:
                badge_class = "bg-danger text-white"
                outcome_text = f"SL HIT ❌ (-{r.get('loss_points', 0):,.1f} pts)"
            else:
                badge_class = "bg-warning text-dark"
                outcome_text = "ACTIVE ⏳ (In Trade)"

            dur_min = r.get("duration_minutes", 0.0)
            exit_time = r.get("exit_time") or "-"
            if is_active:
                dur_text = "In Progress ⏱️"
            elif dur_min > 0:
                time_only = exit_time.split(" ")[-1] if " " in exit_time else exit_time
                dur_text = f"{time_only} ({int(dur_min)} min)"
            else:
                dur_text = exit_time

            enhanced_trades.append({
                "id": r["id"],
                "timestamp": r["timestamp"],
                "date": r["date"],
                "asset": r["asset"],
                "setup_type": r["setup_type"],
                "trigger_price": r["trigger_price"],
                "entry_level": r["entry_level"],
                "target_price": r["target_price"],
                "sl_price": r["sl_price"],
                "status": status,
                "badge_class": badge_class,
                "outcome_text": outcome_text,
                "exit_time": exit_time,
                "duration_text": dur_text,
                "points_captured": r.get("points_captured", 0.0),
                "loss_points": r.get("loss_points", 0.0),
                "duration_minutes": dur_min,
                "notes": r.get("notes", "")
            })

        return {
            "status": "success",
            "summary": stats,
            "dates": dates,
            "trades": enhanced_trades
        }
