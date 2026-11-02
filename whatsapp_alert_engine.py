import os
import sys
import time
import urllib.parse
import threading
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

# Strict Indian Standard Time (IST = UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_str() -> str:
    """Returns current timestamp string formatted strictly in IST."""
    return datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


class WhatsAppAlertService:
    """
    WhatsApp Notification Alert Service for Crypto LTP Terminal & Trade Audit Engine.
    Uses CallMeBot WhatsApp API (and standard webhook).
    Features:
    1. Entry Alert on EOS/EOR level hit when Support/Resistance is Strong.
    2. Outcome Alert on TARGET HIT ✅ or SL HIT ❌ with exit price and points.
    3. Multi-layer Anti-Spam / Debounce protection.
    4. Non-blocking asynchronous message dispatching.
    5. Persistent configuration via SQLite system_config & .env.
    """

    def __init__(self, db=None, cooldown_seconds: int = 1800):
        self.db = db
        self.cooldown_seconds = cooldown_seconds  # 30 minutes debounce per setup
        self._lock = threading.Lock()

        # In-memory tracking for debounce & deduplication
        self._last_sent_times: Dict[str, float] = {}
        self._sent_entry_trade_ids = set()
        self._sent_outcome_trade_ids = set()

        # Load initial configuration from DB and environment
        self._load_config()

    def _load_config(self):
        """Loads configuration from SQLite DB, falling back to environment variables."""
        db_phone = self.db.get_config("whatsapp_phone") if self.db else None
        db_apikey = self.db.get_config("whatsapp_apikey") if self.db else None
        db_enabled = self.db.get_config("whatsapp_enabled") if self.db else None
        db_webhook = self.db.get_config("whatsapp_webhook_url") if self.db else None

        self.phone = (db_phone or os.getenv("WHATSAPP_PHONE", "")).strip()
        self.apikey = (db_apikey or os.getenv("WHATSAPP_APIKEY", "")).strip()
        self.webhook_url = (db_webhook or os.getenv("WHATSAPP_WEBHOOK_URL", "")).strip()

        if db_enabled is not None:
            self.enabled = db_enabled.lower() in ["true", "1", "yes", "on"]
        else:
            env_enabled = os.getenv("WHATSAPP_ENABLED", "true").strip().lower()
            self.enabled = env_enabled in ["true", "1", "yes", "on"]

    def get_config(self) -> Dict[str, Any]:
        """Returns current WhatsApp configuration with masked API key for security."""
        masked_key = ""
        if self.apikey:
            if len(self.apikey) > 4:
                masked_key = self.apikey[:2] + "*" * (len(self.apikey) - 4) + self.apikey[-2:]
            else:
                masked_key = "****"

        return {
            "enabled": self.enabled,
            "phone": self.phone,
            "apikey_masked": masked_key,
            "has_apikey": bool(self.apikey),
            "webhook_url": self.webhook_url,
            "is_configured": bool(self.phone and self.apikey) or bool(self.webhook_url)
        }

    def update_config(
        self,
        phone: Optional[str] = None,
        apikey: Optional[str] = None,
        enabled: Optional[bool] = None,
        webhook_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Updates and persists WhatsApp configuration in DB and memory."""
        with self._lock:
            if phone is not None:
                self.phone = phone.strip()
                if self.db:
                    self.db.set_config("whatsapp_phone", self.phone)

            if apikey is not None:
                cleaned_key = apikey.strip()
                # If user didn't change masked key, keep existing
                if cleaned_key and not cleaned_key.startswith("****"):
                    self.apikey = cleaned_key
                    if self.db:
                        self.db.set_config("whatsapp_apikey", self.apikey)

            if enabled is not None:
                self.enabled = bool(enabled)
                if self.db:
                    self.db.set_config("whatsapp_enabled", "true" if self.enabled else "false")

            if webhook_url is not None:
                self.webhook_url = webhook_url.strip()
                if self.db:
                    self.db.set_config("whatsapp_webhook_url", self.webhook_url)

            # Also try to update .env in root if writable
            self._save_to_dotenv()

        return self.get_config()

    def _save_to_dotenv(self):
        """Helper to sync configuration to .env file for local development persistence."""
        try:
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            keys_to_update = {
                "WHATSAPP_PHONE": self.phone,
                "WHATSAPP_APIKEY": self.apikey,
                "WHATSAPP_ENABLED": "true" if self.enabled else "false",
                "WHATSAPP_WEBHOOK_URL": self.webhook_url
            }

            new_lines = []
            seen_keys = set()
            for line in lines:
                matched = False
                for k, v in keys_to_update.items():
                    if line.startswith(f"{k}=") or line.startswith(f"#{k}="):
                        new_lines.append(f"{k}='{v}'\n")
                        seen_keys.add(k)
                        matched = True
                        break
                if not matched:
                    new_lines.append(line)

            for k, v in keys_to_update.items():
                if k not in seen_keys:
                    new_lines.append(f"{k}='{v}'\n")

            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception as e:
            print(f"[WARNING] Could not update .env: {e}")

    def clean_phone_number(self, phone: str) -> str:
        """Cleans and formats phone number for CallMeBot API (e.g. +919876543210 or 919876543210)."""
        if not phone:
            return ""
        cleaned = "".join(c for c in phone if c.isdigit() or c == "+")
        # Ensure country code format
        if not cleaned.startswith("+") and len(cleaned) == 10:
            cleaned = "+91" + cleaned  # Default India code if 10-digit
        return cleaned

    def send_message_sync(self, text: str) -> Dict[str, Any]:
        """
        Synchronously dispatches WhatsApp message via CallMeBot and/or Webhook.
        Returns status dictionary: {'status': 'success'|'error', 'details': ...}
        """
        if not self.enabled:
            return {"status": "skipped", "message": "WhatsApp notifications are disabled."}

        results = {}
        success = False

        # 1. CallMeBot WhatsApp API
        if self.phone and self.apikey:
            phone_clean = self.clean_phone_number(self.phone)
            url = "https://api.callmebot.com/whatsapp.php"
            params = {
                "phone": phone_clean,
                "text": text,
                "apikey": self.apikey
            }
            try:
                resp = requests.get(url, params=params, timeout=12)
                resp_text = resp.text.strip()
                if resp.status_code == 200 and ("error" not in resp_text.lower() or "queued" in resp_text.lower()):
                    results["callmebot"] = {"status": "success", "response": resp_text}
                    success = True
                else:
                    results["callmebot"] = {"status": "error", "code": resp.status_code, "response": resp_text}
            except Exception as e:
                results["callmebot"] = {"status": "error", "error": str(e)}

        # 2. Custom Webhook Fallback / Secondary channel
        if self.webhook_url:
            try:
                payload = {
                    "text": text,
                    "phone": self.phone,
                    "timestamp": get_ist_str(),
                    "source": "CryptoLTPCalculator"
                }
                resp_w = requests.post(self.webhook_url, json=payload, timeout=8)
                if resp_w.status_code in [200, 201, 204]:
                    results["webhook"] = {"status": "success"}
                    success = True
                else:
                    results["webhook"] = {"status": "error", "code": resp_w.status_code}
            except Exception as e:
                results["webhook"] = {"status": "error", "error": str(e)}

        if not self.phone and not self.webhook_url:
            return {"status": "error", "message": "Neither CallMeBot Phone/APIKey nor Webhook URL is configured."}

        return {
            "status": "success" if success else "error",
            "results": results
        }

    def send_message_async(self, text: str, alert_key: Optional[str] = None) -> None:
        """Asynchronously dispatches message in a background thread without blocking execution."""
        if not self.enabled:
            return

        now = time.time()
        if alert_key:
            with self._lock:
                last_time = self._last_sent_times.get(alert_key, 0)
                if now - last_time < self.cooldown_seconds:
                    print(f"[WHATSAPP DEBOUNCE] Skipped duplicate alert: {alert_key} (Cooldown: {int(self.cooldown_seconds - (now - last_time))}s left)")
                    return
                self._last_sent_times[alert_key] = now

        thread = threading.Thread(
            target=self._worker_send,
            args=(text, alert_key),
            daemon=True,
            name="WhatsAppSenderThread"
        )
        thread.start()

    def _worker_send(self, text: str, alert_key: Optional[str]):
        """Background worker thread executing network request."""
        try:
            res = self.send_message_sync(text)
            status = res.get("status")
            if status == "success":
                print(f"[WHATSAPP SENT] ✅ Successfully dispatched alert ({alert_key or 'Direct'})")
            else:
                print(f"[WHATSAPP WARNING] ⚠️ Dispatch failed or partial: {res}")
        except Exception as e:
            print(f"[WHATSAPP ERROR] ❌ Background sender failed: {e}")

    # =========================================================================
    # TRIGGER 1: ENTRY ALERT (EOS Bullish / EOR Bearish Reversal)
    # =========================================================================
    def send_entry_alert(
        self,
        asset: str,
        setup_type: str,
        entry_price: float,
        target_price: float,
        sl_price: float,
        trigger_time: str,
        trade_id: Optional[int] = None,
        notes: str = ""
    ) -> bool:
        """
        Alert 1: Dispatches instant WhatsApp notification when BTC or ETH hits EOS/EOR level.
        Includes Asset Name, Setup Type, Entry Price, Target Level, Stop Loss Level, Trigger Time (IST).
        Anti-Spam: Debounced per setup and per trade_id.
        """
        if not self.enabled:
            return False

        if trade_id and trade_id in self._sent_entry_trade_ids:
            return False

        # Debounce key by asset, setup_type and approximate entry level (rounded)
        alert_key = f"ENTRY_{asset.upper()}_{setup_type.replace(' ', '_')}_{int(round(entry_price, -1))}"

        now = time.time()
        with self._lock:
            last_time = self._last_sent_times.get(alert_key, 0)
            if now - last_time < self.cooldown_seconds:
                return False
            if trade_id:
                self._sent_entry_trade_ids.add(trade_id)

        asset_name = "Bitcoin (BTC)" if asset.upper() == "BTC" else ("Ethereum (ETH)" if asset.upper() == "ETH" else f"{asset.upper()}")
        is_bullish = "Bullish" in setup_type or "EOS" in setup_type
        header_icon = "🟢" if is_bullish else "🔴"
        action_text = "CALL (CE) BUYING ACTIVE" if is_bullish else "PUT (PE) BUYING ACTIVE"

        message = (
            f"🚨 *CRYPTO LTP ALERT: NEW TRADE SETUP* 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 *Asset:* {asset_name}\n"
            f"📊 *Setup Type:* {header_icon} *{setup_type}*\n"
            f"🎯 *Entry Level:* *${entry_price:,.2f}*\n"
            f"🏁 *Target Level:* *${target_price:,.2f}*\n"
            f"🛡️ *Stop Loss (SL):* *${sl_price:,.2f}*\n"
            f"⏰ *Trigger Time:* {trigger_time} IST\n"
            f"⚡ *Action:* {action_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 *COA Strategy:* Support/Resistance is STRONG.\n"
        )
        if notes:
            message += f"📝 *Context:* {notes}\n"
        message += f"📱 _Ashish Goswami LTP Calculator Pro_"

        self.send_message_async(message, alert_key=alert_key)
        return True

    # =========================================================================
    # TRIGGER 2: OUTCOME ALERT (TARGET HIT ✅ / SL HIT ❌)
    # =========================================================================
    def send_outcome_alert(
        self,
        asset: str,
        setup_type: str,
        outcome: str,
        exit_price: float,
        points: float,
        duration_minutes: float,
        exit_time: str,
        trade_id: Optional[int] = None
    ) -> bool:
        """
        Alert 2: Dispatches instant WhatsApp exit notification when trade hits Target or SL.
        Includes Outcome ('TARGET HIT ✅' or 'SL HIT ❌'), Exit Price, Points Gain/Loss, Duration, IST Time.
        Anti-Spam: Dispatched only ONCE per trade resolution.
        """
        if not self.enabled:
            return False

        if trade_id:
            with self._lock:
                if trade_id in self._sent_outcome_trade_ids:
                    return False
                self._sent_outcome_trade_ids.add(trade_id)

        alert_key = f"OUTCOME_{trade_id or int(time.time())}_{outcome.replace(' ', '_')}"

        asset_name = "Bitcoin (BTC)" if asset.upper() == "BTC" else ("Ethereum (ETH)" if asset.upper() == "ETH" else f"{asset.upper()}")
        is_target = "TARGET" in outcome.upper()

        if is_target:
            header_line = "🎯 *CRYPTO LTP ALERT: TARGET HIT ✅* 🎯"
            pts_line = f"📈 *Points Captured:* *+{points:,.2f} pts* 💰"
        else:
            header_line = "⚠️ *CRYPTO LTP ALERT: STOP LOSS HIT ❌* ⚠️"
            pts_line = f"📉 *Points Loss:* *-{abs(points):,.2f} pts* 🛡️"

        message = (
            f"{header_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 *Asset:* {asset_name}\n"
            f"📊 *Setup:* {setup_type}\n"
            f"🏁 *Outcome:* *{outcome}*\n"
            f"💵 *Exit Price:* *${exit_price:,.2f}*\n"
            f"{pts_line}\n"
            f"⏱️ *Trade Duration:* {duration_minutes:.1f} min\n"
            f"⏰ *Exit Time:* {exit_time} IST\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 _Ashish Goswami LTP Calculator Pro_"
        )

        self.send_message_async(message, alert_key=alert_key)
        return True

    # =========================================================================
    # TEST ALERT SENDER
    # =========================================================================
    def send_test_alert(self) -> Dict[str, Any]:
        """Sends a verification test WhatsApp alert to confirm credentials."""
        now_str = get_ist_str()
        message = (
            f"✅ *CRYPTO LTP CALCULATOR - WHATSAPP ALERT TEST* ✅\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 Congratulations! Your CallMeBot WhatsApp Notification Alert system is successfully connected!\n"
            f"⏰ *Timestamp:* {now_str} IST\n"
            f"🪙 *Active Monitored Assets:* BTC & ETH (Delta Exchange)\n"
            f"🎯 *Features Enabled:*\n"
            f"  • Alert 1: EOS / EOR Entry Trigger Notifications\n"
            f"  • Alert 2: Target Hit & Stop Loss Outcome Notifications\n"
            f"  • Anti-Spam: 30-min Debounce & Trade Deduplication\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 _Ready to receive instant live crypto option reversals!_"
        )
        return self.send_message_sync(message)
