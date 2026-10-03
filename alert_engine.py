import sys
import time
import threading
import subprocess
from datetime import datetime
from typing import List, Dict, Any, Optional

# Fix Windows console UTF-8 output issue
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


HINDI_NUMBER_MAP = {
    0: "शून्य", 1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पांच", 6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस",
    11: "ग्यारह", 12: "बारह", 13: "तेरह", 14: "चौदह", 15: "पंद्रह", 16: "सोलह", 17: "सत्रह", 18: "अठारह", 19: "उन्नीस", 20: "बीस",
    21: "इक्कीस", 22: "बाईस", 23: "तेईस", 24: "चौबीस", 25: "पच्चीस", 26: "छब्बीस", 27: "सत्ताईस", 28: "अट्ठाईस", 29: "उनतीस", 30: "तीस",
    31: "इकत्तीस", 32: "बत्तीस", 33: "तैंतीस", 34: "चौंतीस", 35: "पैंतीस", 36: "छत्तीस", 37: "सैंतीस", 38: "अड़तीस", 39: "उनचालीस", 40: "चालीस",
    41: "इकतालीस", 42: "बयालीस", 43: "तिरालीस", 44: "चौवालिस", 45: "पैंतालीस", 46: "छियालीस", 47: "सैंतालीस", 48: "अड़तालीस", 49: "उनचास", 50: "पचास",
    51: "इक्कावन", 52: "बावन", 53: "तिर्पन", 54: "चौवन", 55: "पचपन", 56: "छप्पन", 57: "सत्तावन", 58: "अट्टावन", 59: "उनसठ", 60: "साठ",
    61: "इक्सठ", 62: "बासठ", 63: "तिरसठ", 64: "चौंसठ", 65: "पैंसठ", 66: "छियासठ", 67: "सरसठ", 68: "अड़सठ", 69: "उनहत्तर", 70: "सत्तर",
    71: "इकहत्तर", 72: "बहत्तर", 73: "तिहत्तर", 74: "चौहत्तर", 75: "पचहत्तर", 76: "छहत्तर", 77: "सतहत्तर", 78: "अठहत्तर", 79: "उनासी", 80: "अस्सी",
    81: "इक्कासी", 82: "बयासी", 83: "तिरासी", 84: "चौरासी", 85: "पचासी", 86: "छियासी", 87: "सत्तासी", 88: "अठासी", 89: "नवासी", 90: "नब्बे",
    91: "इकानवे", 92: "बानवे", 93: "तिरानवे", 94: "चौरानवे", 95: "पंचानवे", 96: "छियानवे", 97: "संतानवे", 98: "अठानवे", 99: "निरानावै"
}


def number_to_hindi_words(num: float) -> str:
    """Converts price numbers like 23041, 22850 into 100% Devanagari Hindi Text Words for gTTS."""
    try:
        val = int(round(num))
        if val <= 0:
            return "शून्य"
        
        parts = []
        lakhs = val // 100000
        rem_lakhs = val % 100000
        if lakhs > 0:
            lakh_txt = HINDI_NUMBER_MAP.get(lakhs, str(lakhs))
            parts.append(f"{lakh_txt} लाख")
            
        thousands = rem_lakhs // 1000
        rem_thousands = rem_lakhs % 1000
        if thousands > 0:
            th_txt = HINDI_NUMBER_MAP.get(thousands, str(thousands))
            parts.append(f"{th_txt} हज़ार")
            
        hundreds = rem_thousands // 100
        rem_hundreds = rem_thousands % 100
        if hundreds > 0:
            h_txt = HINDI_NUMBER_MAP.get(hundreds, str(hundreds))
            parts.append(f"{h_txt} सौ")
            
        if rem_hundreds > 0:
            t_txt = HINDI_NUMBER_MAP.get(rem_hundreds, str(rem_hundreds))
            parts.append(t_txt)
            
        return " ".join(parts)
    except Exception:
        return str(round(num))


class VoiceAlertWorker:
    """Non-blocking background voice alert synthesizer with 10-Minute Anti-Spam Cooldown."""

    def __init__(self, cooldown_seconds: int = 600):
        self.lock = threading.Lock()
        self.last_spoken_time = {}
        self.cooldown_seconds = cooldown_seconds  # 10 minutes (600 seconds) anti-spam cooldown

    def speak_async(self, text: str, alert_key: Optional[str] = None):
        """Asynchronously triggers voice announcement without blocking main execution."""
        now = time.time()
        if alert_key:
            last_time = self.last_spoken_time.get(alert_key, 0)
            if now - last_time < self.cooldown_seconds:
                # Cooldown active, skip duplicate voice alert
                return
            self.last_spoken_time[alert_key] = now

        thread = threading.Thread(target=self._speak_worker, args=(text,), daemon=True)
        thread.start()

    def _speak_worker(self, text: str):
        """Worker thread executing speech command."""
        try:
            safe_text = text.replace("'", "").replace('"', "")
            ps_command = f"Add-Type -AssemblyName System.Speech; $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; $synth.Rate = 0; $synth.Speak('{safe_text}')"
            subprocess.run(["powershell", "-Command", ps_command], capture_output=True, timeout=10)
        except Exception as e:
            print(f"[WARNING] Voice alert worker error: {e}")


class OptionChainAlertEngine:
    """
    Smart Event-Based Voice Alert Engine (Investing Daddy Style).
    Triggers audio ONLY on Genuine Market Events:
    1. Scenario Change
    2. Support/Resistance Status Shift (Strong vs WTT vs WTB)
    3. Reversal Level Proximity Hit (+/- 5 pts of EOS/EOR)
    4. State of Confusion (SOC) Detection
    """

    def __init__(self, proximity_threshold: float = 5.0):
        self.threshold = proximity_threshold
        self.voice = VoiceAlertWorker(cooldown_seconds=600) # 10-Minute Cooldown Filter
        
        # State tracking for Event Detection
        self.prev_scenario: Optional[int] = None
        self.prev_soc_flag: Optional[bool] = None
        self.prev_res_status: Optional[str] = None
        self.prev_supp_status: Optional[str] = None
        self.prev_res_strike: Optional[float] = None
        self.prev_supp_strike: Optional[float] = None

    def evaluate_alerts(self, spot_price: float, records: List[Dict[str, Any]], analysis_res: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Evaluates live snapshot data and returns list of ACTIVE EVENT ALERTS with 10-min deduplication.
        """
        alerts = []
        now_str = datetime.now().strftime("%H:%M:%S")

        res_info = analysis_res.get("resistance", {})
        supp_info = analysis_res.get("support", {})
        coa_info = analysis_res.get("coa_scenario", {})
        eos_info = analysis_res.get("eos", {})
        eor_info = analysis_res.get("eor", {})

        eos_price = eos_info.get("eos_price", 0.0)
        eor_price = eor_info.get("eor_price", 0.0)

        curr_scenario = coa_info.get("scenario")
        scenario_name = coa_info.get("name", "")
        market_state = coa_info.get("market_state", "")

        curr_res_status = res_info.get("overall_status", "STRONG")
        curr_supp_status = supp_info.get("overall_status", "STRONG")
        curr_res_strike = res_info.get("primary_strike", 0.0)
        curr_supp_strike = supp_info.get("primary_strike", 0.0)
        curr_soc = analysis_res.get("soc_detected", False)

        # -------------------------------------------------------------
        # EVENT 1: Scenario Change Alert
        # -------------------------------------------------------------
        if self.prev_scenario is not None and curr_scenario != self.prev_scenario:
            alert_key = f"EVENT_SCENARIO_{curr_scenario}"
            scen_hindi = number_to_hindi_words(curr_scenario)
            title = f"सिनेरियो चेंज: Scenario {curr_scenario}"
            msg = f"Market shifted to Scenario {curr_scenario}: {scenario_name} ({market_state})"
            hindi_voice = f"अलर्ट! मार्किट सिनेरियो बदल कर सिनेरियो {scen_hindi} हो गया है"

            alert_evt = {
                "type": "SCENARIO_CHANGE",
                "severity": "WARNING",
                "title": title,
                "message": msg,
                "voice_text": hindi_voice,
                "alert_key": alert_key,
                "timestamp": now_str
            }
            alerts.append(alert_evt)
            self.voice.speak_async(hindi_voice, alert_key=alert_key)

        self.prev_scenario = curr_scenario

        # -------------------------------------------------------------
        # EVENT 2: State Shift Alert (Support / Resistance Changes)
        # -------------------------------------------------------------
        if self.prev_res_status is not None:
            # Check Resistance Shift
            if curr_res_status != self.prev_res_status or curr_res_strike != self.prev_res_strike:
                alert_key = f"EVENT_RES_SHIFT_{int(curr_res_strike)}_{curr_res_status}"
                title = "रेजिस्टेंस स्टेट चेंज अलर्ट!"
                strike_hindi = number_to_hindi_words(curr_res_strike)
                
                if curr_res_status == "WTT":
                    hindi_voice = f"सूचना! रेजिस्टेंस {strike_hindi} पर वीक टुवर्ड्स टॉप हो गया है"
                elif curr_res_status == "WTB":
                    hindi_voice = f"सूचना! रेजिस्टेंस {strike_hindi} पर वीक टुवर्ड्स बॉटम हो गया है"
                else:
                    hindi_voice = f"सूचना! रेजिस्टेंस {strike_hindi} पर मजबूत हो गया है"

                msg = f"Resistance at Strike {curr_res_strike:,.0f} shifted to {curr_res_status} (Vol: {res_info.get('vol_status')} {res_info.get('vol_ratio_pct', 0):.1f}%)"
                
                alert_evt = {
                    "type": "STATE_SHIFT",
                    "severity": "WARNING",
                    "title": title,
                    "message": msg,
                    "voice_text": hindi_voice,
                    "alert_key": alert_key,
                    "timestamp": now_str
                }
                alerts.append(alert_evt)
                self.voice.speak_async(hindi_voice, alert_key=alert_key)

            # Check Support Shift
            if curr_supp_status != self.prev_supp_status or curr_supp_strike != self.prev_supp_strike:
                alert_key = f"EVENT_SUPP_SHIFT_{int(curr_supp_strike)}_{curr_supp_status}"
                title = "सपोर्ट स्टेट चेंज अलर्ट!"
                strike_hindi = number_to_hindi_words(curr_supp_strike)

                if curr_supp_status == "WTT":
                    hindi_voice = f"सूचना! सपोर्ट {strike_hindi} पर वीक टुवर्ड्स टॉप हो गया है"
                elif curr_supp_status == "WTB":
                    hindi_voice = f"सूचना! सपोर्ट {strike_hindi} पर वीक टुवर्ड्स बॉटम हो गया है"
                else:
                    hindi_voice = f"सूचना! सपोर्ट {strike_hindi} पर मजबूत हो गया है"

                msg = f"Support at Strike {curr_supp_strike:,.0f} shifted to {curr_supp_status} (Vol: {supp_info.get('vol_status')} {supp_info.get('vol_ratio_pct', 0):.1f}%)"

                alert_evt = {
                    "type": "STATE_SHIFT",
                    "severity": "WARNING",
                    "title": title,
                    "message": msg,
                    "voice_text": hindi_voice,
                    "alert_key": alert_key,
                    "timestamp": now_str
                }
                alerts.append(alert_evt)
                self.voice.speak_async(hindi_voice, alert_key=alert_key)

        self.prev_res_status = curr_res_status
        self.prev_supp_status = curr_supp_status
        self.prev_res_strike = curr_res_strike
        self.prev_supp_strike = curr_supp_strike

        # -------------------------------------------------------------
        # EVENT 3: Reversal / Diversion Proximity Hit (+/- 5 pts)
        # -------------------------------------------------------------
        if eos_price > 0 and abs(spot_price - eos_price) <= self.threshold:
            alert_key = f"EVENT_PROX_EOS_{int(round(eos_price))}"
            diff = abs(spot_price - eos_price)
            eos_hindi = number_to_hindi_words(eos_price)
            title = "सपोर्ट का रिवर्सल लेवल आ गया!"
            msg = f"Spot Price ({spot_price:,.2f}) is within {diff:.2f} pts of Extension of Support (EOS: {eos_price:,.2f})"
            hindi_voice = f"ध्यान दें! निफ्टी सपोर्ट के रिवर्सल लेवल {eos_hindi} के पास पहुँच गया है"

            alert_evt = {
                "type": "PROXIMITY",
                "severity": "DANGER",
                "title": title,
                "message": msg,
                "voice_text": hindi_voice,
                "alert_key": alert_key,
                "timestamp": now_str
            }
            alerts.append(alert_evt)
            self.voice.speak_async(hindi_voice, alert_key=alert_key)

        if eor_price > 0 and abs(spot_price - eor_price) <= self.threshold:
            alert_key = f"EVENT_PROX_EOR_{int(round(eor_price))}"
            diff = abs(spot_price - eor_price)
            eor_hindi = number_to_hindi_words(eor_price)
            title = "रेजिस्टेंस का रिवर्सल लेवल आ गया!"
            msg = f"Spot Price ({spot_price:,.2f}) is within {diff:.2f} pts of Extension of Resistance (EOR: {eor_price:,.2f})"
            hindi_voice = f"ध्यान दें! निफ्टी रेजिस्टेंस के रिवर्सल लेवल {eor_hindi} के पास पहुँच गया है"

            alert_evt = {
                "type": "PROXIMITY",
                "severity": "DANGER",
                "title": title,
                "message": msg,
                "voice_text": hindi_voice,
                "alert_key": alert_key,
                "timestamp": now_str
            }
            alerts.append(alert_evt)
            self.voice.speak_async(hindi_voice, alert_key=alert_key)

        # -------------------------------------------------------------
        # EVENT 4: Danger / State of Confusion (SOC) Detection
        # -------------------------------------------------------------
        if self.prev_soc_flag is not None and curr_soc and not self.prev_soc_flag:
            alert_key = "EVENT_SOC_DANGER"
            title = "⚠️ State of Confusion Detected!"
            msg = analysis_res.get("soc_message", "Support/Resistance oscillating rapidly. Avoid new trades.")
            hindi_voice = "सावधान! मार्केट में स्टेट ऑफ कन्फ्यूजन डिटेक्ट हुआ है। कोई नई एंट्री न लें"

            alert_evt = {
                "type": "SOC_ALERT",
                "severity": "DANGER",
                "title": title,
                "message": msg,
                "voice_text": hindi_voice,
                "alert_key": alert_key,
                "timestamp": now_str
            }
            alerts.append(alert_evt)
            self.voice.speak_async(hindi_voice, alert_key=alert_key)

        self.prev_soc_flag = curr_soc

        return alerts
