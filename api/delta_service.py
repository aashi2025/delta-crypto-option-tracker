import os
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple, Optional

IST = timezone(timedelta(hours=5, minutes=30))

class DeltaExchangeService:
    """
    Delta Exchange REST API Service for Crypto Options (BTC / ETH / SOL).
    Fetches real-time Option Chain snapshots, Expiries, Spot Prices, and Greeks.
    """
    def __init__(self, api_key: str = "", api_secret: str = "", base_url: str = "https://api.delta.exchange"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "User-Agent": "DeltaCryptoOptionTracker/1.0"
        }

    def fetch_tickers(self) -> List[Dict[str, Any]]:
        """Fetches all instrument tickers from Delta Exchange."""
        url = f"{self.base_url}/v2/tickers"
        try:
            r = requests.get(url, headers=self._get_headers(), timeout=10)
            if r.status_code == 200:
                return r.json().get("result", [])
        except Exception as e:
            print(f"[ERROR] Failed to fetch Delta tickers: {e}")
        return []

    def get_candles(self, symbol_prefix: str = "BTC", resolution: str = "1", limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetches authentic historical OHLC candles directly from Delta Exchange chart/history API.
        Used for 100% accurate real-time charts on both local and Vercel serverless.
        Returns sorted list: [{'time': int, 'open': float, 'high': float, 'low': float, 'close': float}, ...]
        """
        sym_u = symbol_prefix.upper().replace("DELTA_", "").replace("NSE_INDEX|", "").split(" ")[0]
        if sym_u not in ["BTC", "ETH", "SOL"]:
            sym_u = "BTC"

        primary_sym = f".DE{sym_u}USD" if sym_u in ["BTC", "ETH"] else f"{sym_u}USDT"
        candidates = [primary_sym, f"{sym_u}USDT", f"{sym_u}USD"]
        if sym_u == "BTC":
            candidates = [".DEXBTUSD", "BTCUSDT", "BTCUSD"]
        elif sym_u == "ETH":
            candidates = [".DEETHUSD", "ETHUSDT", "ETHUSD"]
        elif sym_u == "SOL":
            candidates = ["SOLUSDT", ".DESOLUSD", "SOLUSD"]

        # Sanitize resolution for Delta Exchange API ('1', '3', '5', '15', '30', '60', '120', '240', '1D')
        res_clean = str(resolution).strip().lower().replace("m", "").replace("min", "")
        if res_clean in ["1", "3", "5", "15", "30", "60", "120", "240"]:
            sec_per_bar = int(res_clean) * 60
        elif res_clean in ["d", "1d"]:
            res_clean = "1D"
            sec_per_bar = 86400
        else:
            res_clean = "1"
            sec_per_bar = 60

        now = int(time.time())
        from_ts = now - (max(limit, 30) * sec_per_bar)
        url = f"{self.base_url}/v2/chart/history"

        for sym in candidates:
            try:
                r = requests.get(
                    url,
                    params={"symbol": sym, "resolution": res_clean, "from": from_ts, "to": now},
                    headers=self._get_headers(),
                    timeout=8
                )
                if r.status_code == 200:
                    data = r.json().get("result", {})
                    times = data.get("t", [])
                    if times and len(times) > 0:
                        candles = []
                        for i in range(len(times)):
                            candles.append({
                                "time": int(times[i]),
                                "open": round(float(data["o"][i]), 2),
                                "high": round(float(data["h"][i]), 2),
                                "low": round(float(data["l"][i]), 2),
                                "close": round(float(data["c"][i]), 2)
                            })
                        candles.sort(key=lambda x: x["time"])
                        # Deduplicate by timestamp
                        dedup = []
                        seen = set()
                        for c in candles:
                            if c["time"] not in seen:
                                seen.add(c["time"])
                                dedup.append(c)
                        return dedup
            except Exception as e:
                pass
        return []

    def get_upcoming_expiries(self, symbol_prefix: str = "BTC") -> List[str]:
        """Returns sorted list of upcoming option expiry dates for specified symbol (BTC, ETH)."""
        tickers = self.fetch_tickers()
        expiries = set()
        for t in tickers:
            sym = t.get("symbol", "")
            ctype = t.get("contract_type", "")
            if ctype in ["call_options", "put_options"] and f"-{symbol_prefix}-" in sym:
                # Symbol format: C-BTC-87800-031026 -> expiry 031026 (DDMMYY)
                parts = sym.split("-")
                if len(parts) >= 4:
                    exp_raw = parts[-1]
                    try:
                        dt = datetime.strptime(exp_raw, "%d%m%y")
                        exp_str = dt.strftime("%Y-%m-%d")
                        expiries.add(exp_str)
                    except Exception:
                        pass

        sorted_exp = sorted(list(expiries))
        return sorted_exp

    def fetch_option_chain_snapshot(
        self,
        symbol_prefix: str = "BTC",
        expiry_date: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Fetches full Option Chain snapshot for Crypto options (BTC / ETH).
        Returns: (records_with_reversals, metadata_dictionary)
        """
        tickers = self.fetch_tickers()
        expiries = self.get_upcoming_expiries(symbol_prefix)
        
        target_expiry = expiry_date if (expiry_date and expiry_date in expiries) else (expiries[0] if expiries else None)

        spot_price = 0.0
        # Find underlying spot price from tickers (strictly index spot_price first)
        for t in tickers:
            sym = t.get("symbol", "")
            if sym == f"{symbol_prefix}USDT" or sym == f"{symbol_prefix}USD":
                raw_spot = t.get("spot_price")
                if raw_spot and float(raw_spot) > 0:
                    spot_price = float(raw_spot)
                    break

        if spot_price <= 0:
            # Fallback spot price from option tickers (which carry exact index spot_price)
            for t in tickers:
                if f"-{symbol_prefix}-" in t.get("symbol", "") and t.get("spot_price"):
                    raw_spot = t.get("spot_price")
                    if raw_spot and float(raw_spot) > 0:
                        spot_price = float(raw_spot)
                        break

        if spot_price <= 0:
            # Final fallback to mark_price / close if spot_price is completely missing
            for t in tickers:
                sym = t.get("symbol", "")
                if sym == f"{symbol_prefix}USDT" or sym == f"{symbol_prefix}USD":
                    spot_price = float(t.get("mark_price") or t.get("close") or 0.0)
                    if spot_price > 0:
                        break

        # Group Call and Put options by Strike Price
        strikes_map: Dict[float, Dict[str, Any]] = {}

        for t in tickers:
            sym = t.get("symbol", "")
            ctype = t.get("contract_type", "")

            if ctype not in ["call_options", "put_options"] or f"-{symbol_prefix}-" not in sym:
                continue

            parts = sym.split("-")
            if len(parts) < 4:
                continue

            exp_raw = parts[-1]
            try:
                dt = datetime.strptime(exp_raw, "%d%m%y")
                exp_str = dt.strftime("%Y-%m-%d")
            except Exception:
                continue

            if target_expiry and exp_str != target_expiry:
                continue

            strike = float(t.get("strike_price") or 0.0)
            if strike <= 0:
                continue

            if strike not in strikes_map:
                strikes_map[strike] = {
                    "strike_price": strike,
                    "call_ltp": 0.0,
                    "call_volume": 0,
                    "call_oi": 0.0,
                    "call_oi_change": 0.0,
                    "call_iv": 0.0,
                    "put_ltp": 0.0,
                    "put_volume": 0,
                    "put_oi": 0.0,
                    "put_oi_change": 0.0,
                    "put_iv": 0.0,
                }

            rec = strikes_map[strike]
            ltp = float(t.get("close") or t.get("mark_price") or 0.0)

            # Contract Multiplier: 1 contract in Delta Exchange is 0.001 BTC for BTC, 0.01 ETH for ETH
            contract_mult = 1000.0 if symbol_prefix == "BTC" else (100.0 if symbol_prefix == "ETH" else 1.0)
            raw_vol = float(t.get("volume") or 0.0)
            vol = int(round(raw_vol * contract_mult)) if raw_vol < 100 else int(round(raw_vol))

            raw_oi_contracts = float(t.get("oi_contracts") or 0.0)
            raw_oi = float(t.get("oi") or 0.0)
            if raw_oi_contracts > 0:
                oi = int(round(raw_oi_contracts))
            else:
                oi = int(round(raw_oi * contract_mult))

            oi_change = round(float(t.get("oi_change_usd_6h") or 0.0), 2)
            greeks = t.get("greeks") or {}
            iv = float(greeks.get("iv") or t.get("mark_vol") or 0.0) * 100.0

            if ctype == "call_options":
                rec["call_ltp"] = ltp
                rec["call_volume"] = vol
                rec["call_oi"] = oi
                rec["call_oi_change"] = oi_change
                rec["call_iv"] = iv
            elif ctype == "put_options":
                rec["put_ltp"] = ltp
                rec["put_volume"] = vol
                rec["put_oi"] = oi
                rec["put_oi_change"] = oi_change
                rec["put_iv"] = iv

        now_dt = datetime.now(timezone.utc).astimezone(IST)
        now_ts = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        snap_id = now_dt.strftime("%Y%m%d_%H%M%S")
        inst_key = f"DELTA_{symbol_prefix}"
        exp_final = target_expiry or now_dt.strftime("%Y-%m-%d")

        records = sorted(list(strikes_map.values()), key=lambda x: x["strike_price"])
        for r in records:
            r["timestamp"] = now_ts
            r["snapshot_id"] = snap_id
            r["instrument_key"] = inst_key
            r["expiry_date"] = exp_final
            r["spot_price"] = spot_price

        meta = {
            "timestamp": now_ts,
            "snapshot_id": snap_id,
            "instrument_key": inst_key,
            "symbol": symbol_prefix,
            "expiry_date": exp_final,
            "spot_price": spot_price
        }

        return records, meta
