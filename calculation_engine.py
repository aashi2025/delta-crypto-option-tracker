import math
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional


def normal_cdf(x: float) -> float:
    """Cumulative distribution function for standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calculates Black-Scholes Call Option Price."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * normal_cdf(d1) - K * math.exp(-r * T) * normal_cdf(d2)


def black_scholes_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Calculates Black-Scholes Put Option Price."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * normal_cdf(-d2) - S * normal_cdf(-d1)


def solve_bs_implied_spot_call(target_price: float, K: float, T: float, r: float, sigma: float) -> float:
    """Solves for spot price S where Black-Scholes Call Price equals target_price."""
    if target_price <= 0 or T <= 0 or sigma <= 0:
        return K + target_price
    
    low, high = K * 0.5, K * 2.0
    for _ in range(30):
        mid = (low + high) / 2.0
        price = black_scholes_call(mid, K, T, r, sigma)
        if price < target_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def solve_bs_implied_spot_put(target_price: float, K: float, T: float, r: float, sigma: float) -> float:
    """Solves for spot price S where Black-Scholes Put Price equals target_price."""
    if target_price <= 0 or T <= 0 or sigma <= 0:
        return K - target_price
    
    low, high = K * 0.5, K * 2.0
    for _ in range(30):
        mid = (low + high) / 2.0
        price = black_scholes_put(mid, K, T, r, sigma)
        if price < target_price:
            high = mid
        else:
            low = mid
    return (low + high) / 2.0


class OptionChainCalculationEngine:
    """
    Complete Rule Engine for Dr. Vinay Prakash Tiwari's LTP Calculator & Chart of Accuracy (COA 1.0).
    Includes Option Buyer Directives (Strike, Entry, Exit Points).
    """

    def __init__(self, wtt_wtb_threshold: float = 0.75, risk_free_rate: float = 0.07):
        self.threshold = wtt_wtb_threshold  # 75%
        self.r = risk_free_rate
        self.history = {}  # Sliding snapshot history for percentage speed / shifting velocity

    def _calculate_shifting_velocity(self, key_type: str, curr_vol_pct: float, curr_oi_pct: float, overall_status: str) -> Dict[str, Any]:
        """Calculates 5-minute percentage speed / shifting velocity (Gaining Strength vs Weakening)."""
        now_ts = datetime.now()
        hist_key = f"{key_type}_history"
        if hist_key not in self.history:
            self.history[hist_key] = []
        
        max_pct = max(curr_vol_pct, curr_oi_pct)
        self.history[hist_key].append({"ts": now_ts, "pct": max_pct, "status": overall_status})
        
        # Prune records older than 15 minutes
        cutoff = now_ts.timestamp() - 900
        self.history[hist_key] = [h for h in self.history[hist_key] if h["ts"].timestamp() >= cutoff]
        
        hist = self.history[hist_key]
        if len(hist) < 2:
            return {
                "velocity_pct": 0.0,
                "velocity_trend": "STABLE",
                "display_text": "Shifting Stable ⏸️",
                "badge_class": "bg-secondary"
            }
        
        first = hist[0]
        delta_pct = max_pct - first["pct"]
        minutes = max(0.5, (now_ts - first["ts"]).total_seconds() / 60.0)
        rate_per_5m = (delta_pct / minutes) * 5.0

        if overall_status in ["WTT", "WTB"]:
            if rate_per_5m > +0.5:
                trend = "GAINING_STRENGTH"
                display_text = f"Shifting Gaining Strength 📈 (+{rate_per_5m:.1f}%/5m)"
                badge_class = "bg-success text-white"
            elif rate_per_5m < -0.5:
                trend = "WEAKENING"
                display_text = f"Shifting Weakening 📉 ({rate_per_5m:.1f}%/5m)"
                badge_class = "bg-warning text-dark"
            else:
                trend = "STABLE"
                display_text = f"Shifting Stable ⏸️ ({rate_per_5m:+.1f}%/5m)"
                badge_class = "bg-info text-dark"
        else:
            trend = "STRONG_STABLE"
            display_text = "Strong & Stable 🛡️"
            badge_class = "bg-secondary"

        return {
            "velocity_pct": round(rate_per_5m, 2),
            "velocity_trend": trend,
            "display_text": display_text,
            "badge_class": badge_class
        }

    def _analyze_side(self, records: List[Dict[str, Any]], key_type: str) -> Dict[str, Any]:
        vol_key = f"{key_type}_volume"
        oi_key = f"{key_type}_oi"

        sorted_vol = sorted(records, key=lambda x: x[vol_key], reverse=True)
        sorted_oi = sorted(records, key=lambda x: x[oi_key], reverse=True)

        highest_vol = sorted_vol[0] if sorted_vol else None
        second_vol = sorted_vol[1] if len(sorted_vol) > 1 else None

        highest_oi = sorted_oi[0] if sorted_oi else None
        second_oi = sorted_oi[1] if len(sorted_oi) > 1 else None

        # Analyze Volume WTT / WTB
        vol_status = "STRONG"
        vol_ratio = 0.0
        vol_target_strike = None
        if highest_vol and second_vol and highest_vol[vol_key] > 0:
            vol_ratio = second_vol[vol_key] / highest_vol[vol_key]
            if vol_ratio >= self.threshold:
                if second_vol["strike_price"] > highest_vol["strike_price"]:
                    vol_status = "WTT"
                elif second_vol["strike_price"] < highest_vol["strike_price"]:
                    vol_status = "WTB"
                vol_target_strike = second_vol["strike_price"]

        # Analyze OI WTT / WTB
        oi_status = "STRONG"
        oi_ratio = 0.0
        oi_target_strike = None
        if highest_oi and second_oi and highest_oi[oi_key] > 0:
            oi_ratio = second_oi[oi_key] / highest_oi[oi_key]
            if oi_ratio >= self.threshold:
                if second_oi["strike_price"] > highest_oi["strike_price"]:
                    oi_status = "WTT"
                elif second_oi["strike_price"] < highest_oi["strike_price"]:
                    oi_status = "WTB"
                oi_target_strike = second_oi["strike_price"]

        primary_strike = highest_vol["strike_price"] if highest_vol else (highest_oi["strike_price"] if highest_oi else 0.0)

        if vol_status == "WTT" or oi_status == "WTT":
            overall_status = "WTT"
            shifted_strike = vol_target_strike or oi_target_strike
        elif vol_status == "WTB" or oi_status == "WTB":
            overall_status = "WTB"
            shifted_strike = vol_target_strike or oi_target_strike
        else:
            overall_status = "STRONG"
            shifted_strike = None

        vol_pct = vol_ratio * 100.0
        oi_pct = oi_ratio * 100.0

        # Calculate Shifting Velocity / Speed
        velocity_info = self._calculate_shifting_velocity(key_type, vol_pct, oi_pct, overall_status)

        return {
            "primary_strike": primary_strike,
            "shifted_strike": shifted_strike,
            "highest_vol_strike": highest_vol["strike_price"] if highest_vol else 0.0,
            "highest_vol_val": highest_vol[vol_key] if highest_vol else 0,
            "second_vol_strike": second_vol["strike_price"] if second_vol else 0.0,
            "second_vol_val": second_vol[vol_key] if second_vol else 0,
            "vol_ratio_pct": vol_pct,
            "vol_status": vol_status,
            "highest_oi_strike": highest_oi["strike_price"] if highest_oi else 0.0,
            "highest_oi_val": highest_oi[oi_key] if highest_oi else 0,
            "second_oi_strike": second_oi["strike_price"] if second_oi else 0.0,
            "second_oi_val": second_oi[oi_key] if second_oi else 0,
            "oi_ratio_pct": oi_pct,
            "oi_status": oi_status,
            "overall_status": overall_status,
            "shifting_velocity": velocity_info
        }

    def _evaluate_coa2_confirmation(
        self,
        records: List[Dict[str, Any]],
        spot_price: float,
        res_status: str,
        supp_status: str,
        soc_detected: bool
    ) -> Dict[str, Any]:
        """
        COA 2.0 Confirmation Engine:
        Evaluates Call IV vs Put IV comparative bias near ATM to filter false WTT/WTB traps.
        """
        if not records or spot_price <= 0:
            return {
                "status": "NEUTRAL",
                "badge": "⚪ COA 2.0: Neutral",
                "badge_class": "bg-secondary",
                "iv_bias": "NEUTRAL",
                "avg_call_iv": 0.0,
                "avg_put_iv": 0.0,
                "summary": "IV data unavailable."
            }

        # Take ATM +/- 3 strikes
        records_sorted = sorted(records, key=lambda x: abs(x["strike_price"] - spot_price))
        atm_records = records_sorted[:7]

        call_ivs = [r.get("call_iv", 0.0) for r in atm_records if r.get("call_iv", 0.0) > 0]
        put_ivs = [r.get("put_iv", 0.0) for r in atm_records if r.get("put_iv", 0.0) > 0]

        avg_call_iv = sum(call_ivs) / len(call_ivs) if call_ivs else 0.0
        avg_put_iv = sum(put_ivs) / len(put_ivs) if put_ivs else 0.0

        iv_diff = avg_call_iv - avg_put_iv
        
        is_trap = False
        trap_reason = ""

        if res_status == "WTT" and avg_call_iv < (avg_put_iv * 0.95):
            is_trap = True
            trap_reason = "Resistance WTT but Call IV is suppressed (Low Call IV Expansion)"
        elif supp_status == "WTB" and avg_put_iv < (avg_call_iv * 0.95):
            is_trap = True
            trap_reason = "Support WTB but Put IV is suppressed (Low Put IV Expansion)"

        if soc_detected:
            return {
                "status": "NO_TRADE",
                "badge": "⚠️ COA 2.0: Trap / State of Confusion",
                "badge_class": "bg-danger text-white",
                "iv_bias": "CONFLICT",
                "avg_call_iv": round(avg_call_iv, 2),
                "avg_put_iv": round(avg_put_iv, 2),
                "summary": "State of Confusion: Volume & OI Conflict with IV Discrepancy."
            }
        elif is_trap:
            return {
                "status": "TRAP_WARNING",
                "badge": "⚠️ COA 2.0 TRAP WARNING: Unconfirmed Shifting",
                "badge_class": "bg-warning text-dark",
                "iv_bias": "IV_DIVERGENCE",
                "avg_call_iv": round(avg_call_iv, 2),
                "avg_put_iv": round(avg_put_iv, 2),
                "summary": f"False Trap Warning! {trap_reason}."
            }
        else:
            bias_text = "Bullish IV Bias" if iv_diff > 0.5 else ("Bearish IV Bias" if iv_diff < -0.5 else "Balanced IV")
            return {
                "status": "CONFIRMED",
                "badge": f"✅ COA 2.0 CONFIRMED ({bias_text})",
                "badge_class": "bg-success text-white",
                "iv_bias": bias_text,
                "avg_call_iv": round(avg_call_iv, 2),
                "avg_put_iv": round(avg_put_iv, 2),
                "summary": f"COA 2.0 Confirmed: Call IV ({avg_call_iv:.1f}%) vs Put IV ({avg_put_iv:.1f}%) aligns with scenario."
            }

    def _determine_buyer_directive(self, scenario_num: int, supp_strike: float, res_strike: float, eos_price: float, eor_price: float, ur_price: float, us_price: float, soc_detected: bool) -> Dict[str, Any]:
        """
        Determines Option Buyer's exact directive: Trade Type (BUY CE / BUY PE / NO TRADE),
        Recommended Strike Price, Entry Point, Exit/Target Point, and Calculated Stop Loss (SL).
        """
        buffer_pts = 15.0  # 15 index points risk buffer
        
        if soc_detected or scenario_num in [6, 8]:
            return {
                "action_type": "NO_TRADE",
                "badge": "⚠️ NO TRADE ZONE",
                "badge_class": "bg-danger text-white",
                "strike": "कोई स्ट्राइक नहीं (No Trade Zone)",
                "entry": "NO ENTRY (मार्केट कन्फ्यूज्ड है)",
                "exit": "NO EXIT (ट्रेड ना लें)",
                "sl": "N/A (कोई ट्रेड नहीं)",
                "summary": "मार्केट कन्फ्यूज्ड या State of Confusion में है। कोई नया trade ना लें।"
            }

        if scenario_num == 1:
            ce_sl = max(0.0, eos_price - buffer_pts)
            pe_sl = eor_price + buffer_pts
            return {
                "action_type": "RANGEBOUND",
                "badge": "🔄 RANGEBOUND TRADING",
                "badge_class": "bg-info text-dark",
                "strike": f"{supp_strike:,.0f} CE (at EOS) / {res_strike:,.0f} PE (at EOR)",
                "entry": f"Call एंट्री: EOS ({eos_price:,.2f}) | Put एंट्री: EOR ({eor_price:,.2f})",
                "exit": f"Call टारगेट: EOR ({eor_price:,.2f}) | Put टारगेट: EOS ({eos_price:,.2f})",
                "sl": f"Call SL: {ce_sl:,.2f} | Put SL: {pe_sl:,.2f} (Risk: ~{buffer_pts:g} pts)",
                "summary": f"सपोर्ट EOS ({eos_price:,.2f}) से Call खरीदें या रेजिस्टेंस EOR ({eor_price:,.2f}) से Put खरीदें।"
            }
        elif scenario_num in [2, 4, 5]:
            target_p = ur_price if scenario_num == 5 else eor_price
            ce_sl = round(eos_price - buffer_pts, 2)
            risk_pts = round(eos_price - ce_sl, 1)
            return {
                "action_type": "BUY_CE",
                "badge": "🟢 BUY CALL (CE)",
                "badge_class": "bg-success text-white",
                "strike": f"{supp_strike:,.0f} CE (ATM / ITM Call)",
                "entry": f"EOS ({eos_price:,.2f}) पर खरीदें",
                "exit": f"Target EOR / UR ({target_p:,.2f})",
                "sl": f"{ce_sl:,.2f} (Risk: ~{risk_pts:g} pts)",
                "summary": f"बुलिश ट्रेंड: EOS ({eos_price:,.2f}) के पास {supp_strike:,.0f} Call (CE) खरीदें। टारगेट {target_p:,.2f} रहेगा।"
            }
        elif scenario_num in [3, 7, 9]:
            target_p = us_price if scenario_num == 9 else eos_price
            pe_sl = round(eor_price + buffer_pts, 2)
            risk_pts = round(pe_sl - eor_price, 1)
            return {
                "action_type": "BUY_PE",
                "badge": "🔴 BUY PUT (PE)",
                "badge_class": "bg-danger text-white",
                "strike": f"{res_strike:,.0f} PE (ATM / ITM Put)",
                "entry": f"EOR ({eor_price:,.2f}) पर खरीदें",
                "exit": f"Target EOS / US ({target_p:,.2f})",
                "sl": f"{pe_sl:,.2f} (Risk: ~{risk_pts:g} pts)",
                "summary": f"बेरिश ट्रेंड: EOR ({eor_price:,.2f}) के पास {res_strike:,.0f} Put (PE) खरीदें। टारगेट {target_p:,.2f} रहेगा।"
            }
        else:
            return {
                "action_type": "NO_TRADE",
                "badge": "⚠️ OBSERVATION MODE",
                "badge_class": "bg-warning text-dark",
                "strike": "Observe Strikes",
                "entry": "Wait for clear signal",
                "exit": "Observe Reversal Levels",
                "sl": "N/A (Wait for signal)",
                "summary": "क्लियर डाइवर्जन सिग्नल का इंतज़ार करें।"
            }

    def _determine_coa_scenario(self, res_status: str, supp_status: str, vol_oi_conflict: bool = False) -> Dict[str, Any]:
        scenarios = {
            ("STRONG", "STRONG"): {
                "scenario": 1,
                "name": "Scenario 1: Support Strong, Resistance Strong",
                "market_state": "RANGEBOUND (BUY AT EOS, SELL AT EOR)",
                "trend": "SIDEWAYS / RANGEBOUND",
                "trading_action": "Market Rangebound (Buy Call at EOS, Sell Put/PE at EOR)",
                "hindi_voice": "मार्केट रेंज बाउंड है। सपोर्ट के एक्सटेंशन से कॉल खरीदें और रेजिस्टेंस के एक्सटेंशन से पुट खरीदें।"
            },
            ("STRONG", "WTT"): {
                "scenario": 2,
                "name": "Scenario 2: Support Strong, Resistance WTT",
                "market_state": "BULLISH MARKET",
                "trend": "BULLISH UPTREND",
                "trading_action": "Bullish Market (Buy Call from EOS and every Diversion. Target EOR+1)",
                "hindi_voice": "मार्केट बुलिश है। सपोर्ट और हर डाइवर्जन से कॉल खरीदें। रेजिस्टेंस के ऊपर तक का टारगेट रहेगा।"
            },
            ("STRONG", "WTB"): {
                "scenario": 3,
                "name": "Scenario 3: Support Strong, Resistance WTB",
                "market_state": "SELL ON RISE",
                "trend": "DOWNTREND FROM RESISTANCE",
                "trading_action": "Sell on Rise (Sell Put/Buy PE from EOR. Market may fall up to Support)",
                "hindi_voice": "मार्केट में सेल ऑन राइज है। रेजिस्टेंस से पुट खरीदें, मार्केट सपोर्ट तक गिर सकता है।"
            },
            ("WTT", "STRONG"): {
                "scenario": 4,
                "name": "Scenario 4: Support WTT, Resistance Strong",
                "market_state": "BULLISH / BUY ON DIPS",
                "trend": "BULLISH UPTREND (RESISTANCE BREAKOUT)",
                "trading_action": "Bullish/Buy on Dips (Buy CE from Extension of Support. Market will break Resistance)",
                "hindi_voice": "मार्केट में बाय ऑन डिप्स है। सपोर्ट से कॉल खरीदें, रेजिस्टेंस टूटेगा।"
            },
            ("WTT", "WTT"): {
                "scenario": 5,
                "name": "Scenario 5: Support WTT, Resistance WTT",
                "market_state": "EXTREMELY BULLISH",
                "trend": "STRONG UPTREND",
                "trading_action": "Extremely Bullish (Buy from every dip/Diversion. Do not sell PE)",
                "hindi_voice": "मार्केट बहुत ज्यादा बुलिश है। हर गिरावट पर कॉल खरीदें, पुट बिल्कुल ना लें।"
            },
            ("WTT", "WTB"): {
                "scenario": 6,
                "name": "Scenario 6: Support WTT, Resistance WTB",
                "market_state": "MARKET CONFUSED / REVERSAL RESTRICTED",
                "trend": "VOLATILE",
                "trading_action": "Market Confused / Reversal point restricted",
                "hindi_voice": "मार्केट कन्फ्यूज्ड है। रिवर्सल पॉइंट सीमित है, संभलकर ट्रेड करें।"
            },
            ("WTB", "STRONG"): {
                "scenario": 7,
                "name": "Scenario 7: Support WTB, Resistance Strong",
                "market_state": "BEARISH MARKET",
                "trend": "DOWNTREND (SUPPORT BREAKDOWN)",
                "trading_action": "Bearish Market (Sell PE / Buy PE from EOR. Support will break down)",
                "hindi_voice": "मार्केट बेरिश है। रेजिस्टेंस से पुट खरीदें, सपोर्ट टूट सकता है।"
            },
            ("WTB", "WTT"): {
                "scenario": 8,
                "name": "Scenario 8: Support WTB, Resistance WTT",
                "market_state": "STATE OF CONFUSION / NO TRADE ZONE",
                "trend": "CONFLICT / SQUEEZE",
                "trading_action": "Market Confused / Volatile between ranges (State of Confusion - No Trade Zone)",
                "hindi_voice": "सावधान! मार्केट में स्टेट ऑफ कन्फ्यूजन है। कोई नया ट्रेड ना लें।"
            },
            ("WTB", "WTB"): {
                "scenario": 9,
                "name": "Scenario 9: Support WTB, Resistance WTB",
                "market_state": "EXTREMELY BEARISH / BLOODBATH",
                "trend": "STRONG DOWNTREND",
                "trading_action": "Extremely Bearish / Bloodbath (Sell from EOR and every upward Diversion. No CE buying)",
                "hindi_voice": "मार्केट में भारी गिरावट यानी ब्लडबाथ है। हर तेजी पर पुट खरीदें, कॉल बिल्कुल ना लें।"
            }
        }

        key = (res_status, supp_status)
        match = scenarios.get(key, {
            "scenario": 0,
            "name": f"Custom Scenario: R={res_status}, S={supp_status}",
            "market_state": "NEUTRAL / UNKNOWN",
            "trend": "NEUTRAL",
            "trading_action": "Observe price action at key diversion levels.",
            "hindi_voice": "मार्केट न्यूट्रल है। डाइवर्जन लेवल्स पर प्राइस एक्शन देखें।"
        })

        if vol_oi_conflict or key == ("WTB", "WTT"):
            match["soc_detected"] = True
            match["soc_message"] = "State of Confusion - No Trade Zone (Volume & OI conflict)"
        else:
            match["soc_detected"] = False
            match["soc_message"] = ""

        return match

    def analyze(self, records: List[Dict[str, Any]], spot_price: float, expiry_date_str: str) -> Dict[str, Any]:
        if not records:
            return {}

        resistance_info = self._analyze_side(records, "call")
        support_info = self._analyze_side(records, "put")

        res_strike = resistance_info["primary_strike"]
        supp_strike = support_info["primary_strike"]

        vol_oi_conflict = (
            (resistance_info["vol_status"] == "WTT" and resistance_info["oi_status"] == "WTB") or
            (resistance_info["vol_status"] == "WTB" and resistance_info["oi_status"] == "WTT") or
            (support_info["vol_status"] == "WTT" and support_info["oi_status"] == "WTB") or
            (support_info["vol_status"] == "WTB" and support_info["oi_status"] == "WTT")
        )

        coa_info = self._determine_coa_scenario(
            resistance_info["overall_status"],
            support_info["overall_status"],
            vol_oi_conflict=vol_oi_conflict
        )

        days_to_expiry = 1.0 / 365.0
        try:
            exp_date = datetime.strptime(expiry_date_str, "%Y-%m-%d")
            now = datetime.now()
            days = max(1, (exp_date - now).days + 1)
            days_to_expiry = days / 365.0
        except Exception:
            pass

        records_with_reversals = []
        for r in records:
            strike = r["strike_price"]
            call_ltp = r.get("call_ltp", 0.0)
            put_ltp = r.get("put_ltp", 0.0)
            call_iv = max(0.05, r.get("call_iv", 0.0) / 100.0)
            put_iv = max(0.05, r.get("put_iv", 0.0) / 100.0)

            call_diversion_simple = strike + call_ltp
            put_diversion_simple = strike - put_ltp

            call_bs_reversal = solve_bs_implied_spot_call(call_ltp, strike, days_to_expiry, self.r, call_iv)
            put_bs_reversal = solve_bs_implied_spot_put(put_ltp, strike, days_to_expiry, self.r, put_iv)

            rec_copy = dict(r)
            rec_copy["call_diversion"] = round(call_diversion_simple, 2)
            rec_copy["put_diversion"] = round(put_diversion_simple, 2)
            rec_copy["call_bs_reversal"] = round(call_bs_reversal, 2)
            rec_copy["put_bs_reversal"] = round(put_bs_reversal, 2)
            records_with_reversals.append(rec_copy)

        supp_rec = next((r for r in records_with_reversals if r["strike_price"] == supp_strike), None)
        res_rec = next((r for r in records_with_reversals if r["strike_price"] == res_strike), None)

        eos_price = supp_rec["put_diversion"] if supp_rec else (supp_strike - 50.0)
        eor_price = res_rec["call_diversion"] if res_rec else (res_strike + 50.0)

        ur_strike = resistance_info.get("shifted_strike") if resistance_info["overall_status"] == "WTT" else res_strike
        ur_rec = next((r for r in records_with_reversals if r["strike_price"] == ur_strike), None) if ur_strike else res_rec
        ur_price = ur_rec["call_diversion"] if ur_rec else eor_price

        us_strike = support_info.get("shifted_strike") if support_info["overall_status"] == "WTB" else supp_strike
        us_rec = next((r for r in records_with_reversals if r["strike_price"] == us_strike), None) if us_strike else supp_rec
        us_price = us_rec["put_diversion"] if us_rec else eos_price

        # Option Buyer Directive
        buyer_directive = self._determine_buyer_directive(
            coa_info.get("scenario", 0),
            supp_strike,
            res_strike,
            eos_price,
            eor_price,
            ur_price,
            us_price,
            coa_info.get("soc_detected", False)
        )

        # Evaluate COA 2.0 Confirmation (IV Comparative Bias & Trap Filter)
        coa2_confirmation = self._evaluate_coa2_confirmation(
            records_with_reversals,
            spot_price,
            resistance_info["overall_status"],
            support_info["overall_status"],
            coa_info.get("soc_detected", False)
        )

        analysis_result = {
            "spot_price": spot_price,
            "expiry_date": expiry_date_str,
            "resistance": resistance_info,
            "support": support_info,
            "coa_scenario": coa_info,
            "coa2_confirmation": coa2_confirmation,
            "buyer_directive": buyer_directive,
            "soc_detected": coa_info.get("soc_detected", False),
            "soc_message": coa_info.get("soc_message", ""),
            "eos": {
                "strike": supp_strike,
                "eos_price": eos_price,
                "eos_bs_price": supp_rec["put_bs_reversal"] if supp_rec else eos_price
            },
            "eor": {
                "strike": res_strike,
                "eor_price": eor_price,
                "eor_bs_price": res_rec["call_bs_reversal"] if res_rec else eor_price
            },
            "ur": {
                "strike": ur_strike,
                "ur_price": ur_price
            },
            "us": {
                "strike": us_strike,
                "us_price": us_price
            },
            "records_with_reversals": records_with_reversals
        }

        return analysis_result

    def analyze_920_strategy(self, records: List[Dict[str, Any]], spot_price: float, expiry_date_str: str) -> Dict[str, Any]:
        """
        Calculates Dr. Vinay Prakash Tiwari's 9:20 AM Morning Reversal Strategy.
        Rules:
        - Call Side: Identify Highest Volume & Highest OI (9:20 Resistance Strike).
        - Put Side: Identify Highest Volume & Highest OI (9:20 Support Strike).
        - Calculate 9:20 EOS & 9:20 EOR.
        - Evaluate 9:20 Setup Directive (CE Buy at EOS, PE Buy at EOR, or Invalid/Avoid if high shifting volatility).
        """
        if not records:
            return {}

        analysis_full = self.analyze(records, spot_price, expiry_date_str)
        res_info = analysis_full.get("resistance", {})
        supp_info = analysis_full.get("support", {})
        eos_info = analysis_full.get("eos", {})
        eor_info = analysis_full.get("eor", {})

        res_strike = res_info.get("primary_strike", 0.0)
        supp_strike = supp_info.get("primary_strike", 0.0)
        eos_920 = eos_info.get("eos_price", 0.0)
        eor_920 = eor_info.get("eor_price", 0.0)

        # Extract records with reversals & sort by strike
        records_with_reversals = analysis_full.get("records_with_reversals", records)
        records_sorted = sorted(records_with_reversals, key=lambda x: x["strike_price"])

        # 1. 9:20 R1 (Primary Resistance / EOR)
        r1_920 = eor_920

        # 2. 9:20 S1 (Primary Support / EOS)
        s1_920 = eos_920

        # 3. 9:20 R2 (Upper Resistance / R+1) - 1 strike above Resistance Call Diversion
        r2_rec = next((r for r in records_sorted if r["strike_price"] > res_strike), None)
        if r2_rec:
            r2_920 = r2_rec.get("call_diversion", r2_rec["strike_price"] + r2_rec.get("call_ltp", 0.0))
        else:
            r2_920 = r1_920 + 50.0
        if r2_920 <= r1_920:
            r2_920 = r1_920 + 50.0

        # 4. 9:20 S2 (Lower Support / S-1) - 1 strike below Support Put Diversion
        s2_recs = [r for r in records_sorted if r["strike_price"] < supp_strike]
        s2_rec = s2_recs[-1] if s2_recs else None
        if s2_rec:
            s2_920 = s2_rec.get("put_diversion", s2_rec["strike_price"] - s2_rec.get("put_ltp", 0.0))
        else:
            s2_920 = s1_920 - 50.0
        if s2_920 >= s1_920:
            s2_920 = s1_920 - 50.0

        # 5. 9:20 Mid-Diversion (Center Target Line / Mid-Pivot)
        if supp_strike < res_strike:
            supp_rec = next((r for r in records_sorted if r["strike_price"] == supp_strike), None)
            if supp_rec and supp_rec.get("call_diversion", 0) > 0:
                mid_920 = supp_rec["call_diversion"]
            else:
                mid_920 = (r1_920 + s1_920) / 2.0
        else:
            mid_920 = (r1_920 + s1_920) / 2.0

        if not (s1_920 < mid_920 < r1_920):
            mid_920 = (r1_920 + s1_920) / 2.0

        res_status = res_info.get("overall_status", "STRONG")
        supp_status = supp_info.get("overall_status", "STRONG")
        res_ratio = max(res_info.get("vol_ratio_pct", 0), res_info.get("oi_ratio_pct", 0))
        supp_ratio = max(supp_info.get("vol_ratio_pct", 0), supp_info.get("oi_ratio_pct", 0))

        is_high_volatility = (res_status == "WTB" and res_ratio > 80.0) or (supp_status == "WTT" and supp_ratio > 80.0)

        directive_action = "WAIT / AVOID"
        directive_badge = "WAIT FOR 9:20 EOS / EOR LEVEL"
        badge_class = "bg-warning text-dark"
        strike_to_buy = f"{supp_strike:.0f} CE" if supp_strike > 0 else "-"
        entry_point = eos_920
        target_exit = eor_920
        sl_point = eos_920 - 15.0 if eos_920 > 0 else 0.0
        status_msg = "9:20 AM Base Levels Locked. Watch for morning reversal near EOS / EOR."

        if is_high_volatility:
            directive_action = "INVALID SETUP"
            directive_badge = "9:20 SETUP INVALID - HIGH SHIFTING VOLATILITY"
            badge_class = "bg-danger text-white"
            status_msg = "High Shifting Volatility (>80%) detected at 9:20 AM. Morning Reversal Setup Invalid!"
        else:
            if eos_920 > 0 and abs(spot_price - eos_920) <= 25.0:
                directive_action = "BUY CALL (CE)"
                directive_badge = f"9:20 CE TRADE ACTIVE AT {eos_920:.2f}"
                badge_class = "bg-success text-white"
                strike_to_buy = f"{supp_strike:.0f} CE"
                entry_point = eos_920
                target_exit = eor_920
                sl_point = eos_920 - 15.0
                status_msg = f"Spot price is at 9:20 EOS ({eos_920:.2f}). 9:20 Morning CE Trade Active!"
            elif eor_920 > 0 and abs(spot_price - eor_920) <= 25.0:
                directive_action = "BUY PUT (PE)"
                directive_badge = f"9:20 PE TRADE ACTIVE AT {eor_920:.2f}"
                badge_class = "bg-danger text-white"
                strike_to_buy = f"{res_strike:.0f} PE"
                entry_point = eor_920
                target_exit = eos_920
                sl_point = eor_920 + 15.0
                status_msg = f"Spot price is at 9:20 EOR ({eor_920:.2f}). 9:20 Morning PE Trade Active!"
            else:
                directive_action = "WAIT / STANDBY"
                directive_badge = f"BUY CE AT {eos_920:.1f} / BUY PE AT {eor_920:.1f}"
                badge_class = "bg-info text-dark"
                strike_to_buy = f"{supp_strike:.0f} CE / {res_strike:.0f} PE"

        return {
            "spot_price_920": spot_price,
            "res_strike_920": res_strike,
            "supp_strike_920": supp_strike,
            "res_status_920": res_status,
            "supp_status_920": supp_status,
            "eos_920": round(eos_920, 2),
            "eor_920": round(eor_920, 2),
            "r2_920": round(r2_920, 2),
            "r1_920": round(r1_920, 2),
            "mid_920": round(mid_920, 2),
            "s1_920": round(s1_920, 2),
            "s2_920": round(s2_920, 2),
            "directive_action": directive_action,
            "directive_badge": directive_badge,
            "badge_class": badge_class,
            "strike_to_buy": strike_to_buy,
            "entry_point": round(entry_point, 2),
            "target_exit": round(target_exit, 2),
            "sl_point": round(sl_point, 2),
            "status_msg": status_msg,
            "is_high_volatility": is_high_volatility
        }

