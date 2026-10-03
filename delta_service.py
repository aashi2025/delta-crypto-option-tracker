import os
import time
import requests
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

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
            vol = int(float(t.get("volume") or 0.0))
            oi = float(t.get("oi") or t.get("oi_contracts") or 0.0)
            oi_change = float(t.get("oi_change_usd_6h") or 0.0)
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

        now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        snap_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        inst_key = f"DELTA_{symbol_prefix}"
        exp_final = target_expiry or datetime.now().strftime("%Y-%m-%d")

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
