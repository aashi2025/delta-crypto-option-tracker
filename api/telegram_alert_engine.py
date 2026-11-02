import os
import sys
import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Strict Indian Standard Time (IST = UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_str() -> str:
    """Returns current timestamp string formatted strictly in IST."""
    return datetime.now(timezone.utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")


class TelegramAlertService:
    """
    Telegram Notification Alert Service for Crypto LTP Terminal & Trade Audit Engine.
    Uses official Telegram Bot API (https://api.telegram.org/bot<TOKEN>/sendMessage).
    
    Features:
    1. Automatic Entry Alert on EOS/EOR level hit when Support/Resistance is Strong.
    2. Automatic Outcome Alert on TARGET HIT ✅ or SL HIT ❌ with points captured/lost.
    3. On-Demand Interactive Telegram Bot Commands (/status, /btc, /eth, /levels, /trades):
       - User Telegram me command bhejkar kisi bhi samay live levels aur active trades manga sakta hai!
    4. One-Click On-Demand dispatch from Web Dashboard / API.
    5. Multi-layer Anti-Spam / Debounce protection (30 min cooldown + deduplication).
    6. Non-blocking asynchronous message dispatching.
    7. Persistent configuration via SQLite system_config & .env.
    """

    DEFAULT_BOT_TOKEN = "9074898916:AAFRMxWBsl4d3rO6uoo-dJ_9UQPJut_8chQ"
    DEFAULT_CHAT_ID = "5246742802"

    def __init__(self, db=None, cooldown_seconds: int = 1800, service=None, calc_engine=None):
        self.db = db
        self.service = service
        self.calc_engine = calc_engine
        self.cooldown_seconds = cooldown_seconds  # 30 minutes debounce per setup
        self._lock = threading.Lock()

        # In-memory tracking for debounce & deduplication
        self._last_sent_times: Dict[str, float] = {}
        self._sent_entry_trade_ids = set()
        self._sent_outcome_trade_ids = set()
        self._bot_info: Optional[Dict[str, Any]] = None

        # Interactive Bot Listener state
        self._listener_running = False
        self._listener_thread: Optional[threading.Thread] = None
        self._last_update_id: Optional[int] = None

        # Load initial configuration from DB and environment
        self._load_config()

        # Verify bot connection in background
        threading.Thread(target=self.verify_bot_connection, daemon=True).start()

    def set_market_provider(self, service=None, calc_engine=None, db=None):
        """Sets references to DeltaExchangeService, OptionChainCalculationEngine, and OptionChainDB."""
        if service:
            self.service = service
        if calc_engine:
            self.calc_engine = calc_engine
        if db:
            self.db = db

    def _load_config(self):
        """Loads configuration from SQLite DB, falling back to environment variables or defaults."""
        db_token = self.db.get_config("telegram_bot_token") if self.db else None
        db_chat_id = self.db.get_config("telegram_chat_id") if self.db else None
        db_enabled = self.db.get_config("telegram_enabled") if self.db else None

        self.bot_token = (db_token or os.getenv("TELEGRAM_BOT_TOKEN", self.DEFAULT_BOT_TOKEN)).strip()
        self.chat_id = (db_chat_id or os.getenv("TELEGRAM_CHAT_ID", self.DEFAULT_CHAT_ID)).strip()

        if db_enabled is not None:
            self.enabled = db_enabled.lower() in ["true", "1", "yes", "on"]
        else:
            env_enabled = os.getenv("TELEGRAM_ENABLED", "true").strip().lower()
            self.enabled = env_enabled in ["true", "1", "yes", "on"]

        # Ensure values are saved to DB if DB is active and values were missing
        if self.db:
            try:
                if not db_token and self.bot_token:
                    self.db.set_config("telegram_bot_token", self.bot_token)
                if not db_chat_id and self.chat_id:
                    self.db.set_config("telegram_chat_id", self.chat_id)
                if db_enabled is None:
                    self.db.set_config("telegram_enabled", "true" if self.enabled else "false")
            except Exception as _e:
                print(f"[WARNING] Failed to persist initial telegram config to DB: {_e}")

    def get_config(self) -> Dict[str, Any]:
        """Returns current Telegram configuration with masked token for security."""
        masked_token = ""
        if self.bot_token:
            if len(self.bot_token) > 10:
                masked_token = self.bot_token[:6] + "*" * (len(self.bot_token) - 10) + self.bot_token[-4:]
            else:
                masked_token = "******"

        return {
            "enabled": self.enabled,
            "bot_token_masked": masked_token,
            "has_token": bool(self.bot_token),
            "chat_id": self.chat_id,
            "is_configured": bool(self.bot_token and self.chat_id),
            "bot_username": self._bot_info.get("username") if self._bot_info else "Bhartiyestockalertbot",
            "bot_name": self._bot_info.get("first_name") if self._bot_info else "Indianstockalert",
            "listener_active": self._listener_running
        }

    def update_config(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: Optional[bool] = None
    ) -> Dict[str, Any]:
        """Updates and persists Telegram configuration in DB, memory, and .env."""
        with self._lock:
            if bot_token is not None:
                cleaned_token = bot_token.strip()
                if cleaned_token and not cleaned_token.startswith("***") and "*" not in cleaned_token:
                    self.bot_token = cleaned_token
                    self._bot_info = None  # Reset cached bot info
                    if self.db:
                        self.db.set_config("telegram_bot_token", self.bot_token)

            if chat_id is not None:
                self.chat_id = chat_id.strip()
                if self.db:
                    self.db.set_config("telegram_chat_id", self.chat_id)

            if enabled is not None:
                self.enabled = bool(enabled)
                if self.db:
                    self.db.set_config("telegram_enabled", "true" if self.enabled else "false")

            self._save_to_dotenv()

        # Refresh bot info in background
        threading.Thread(target=self.verify_bot_connection, daemon=True).start()
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
                "TELEGRAM_BOT_TOKEN": self.bot_token,
                "TELEGRAM_CHAT_ID": self.chat_id,
                "TELEGRAM_ENABLED": "true" if self.enabled else "false"
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
            print(f"[WARNING] Could not update .env with telegram settings: {e}")

    def verify_bot_connection(self) -> Dict[str, Any]:
        """Calls getMe on Telegram API to verify bot token and cache bot username."""
        if not self.bot_token:
            return {"status": "error", "message": "Telegram Bot Token is missing."}

        url = f"https://api.telegram.org/bot{self.bot_token}/getMe"
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                self._bot_info = data.get("result", {})
                return {"status": "success", "bot": self._bot_info}
            else:
                return {"status": "error", "message": data.get("description", "Failed to verify bot token")}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def send_message_sync(self, text: str, parse_mode: str = "HTML", target_chat_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Synchronously dispatches Telegram message to configured Chat ID.
        Returns status dictionary: {'status': 'success'|'error', 'details': ...}
        """
        if not self.enabled:
            return {"status": "skipped", "message": "Telegram notifications are disabled."}

        dest_chat_id = target_chat_id or self.chat_id
        if not self.bot_token or not dest_chat_id:
            return {"status": "error", "message": "Telegram Bot Token or Chat ID is not configured."}

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": dest_chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }

        try:
            resp = requests.post(url, json=payload, timeout=12)
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                return {"status": "success", "result": data.get("result")}
            else:
                desc = data.get("description", resp.text)
                return {"status": "error", "code": resp.status_code, "message": desc}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def send_message_async(self, text: str, alert_key: Optional[str] = None, parse_mode: str = "HTML", target_chat_id: Optional[str] = None) -> None:
        """Asynchronously dispatches message in a background thread without blocking execution."""
        if not self.enabled:
            return

        now = time.time()
        if alert_key:
            with self._lock:
                last_time = self._last_sent_times.get(alert_key, 0)
                if now - last_time < self.cooldown_seconds:
                    print(f"[TELEGRAM DEBOUNCE] Skipped duplicate alert: {alert_key} (Cooldown: {int(self.cooldown_seconds - (now - last_time))}s left)")
                    return
                self._last_sent_times[alert_key] = now

        thread = threading.Thread(
            target=self._worker_send,
            args=(text, alert_key, parse_mode, target_chat_id),
            daemon=True,
            name="TelegramSenderThread"
        )
        thread.start()

    def _worker_send(self, text: str, alert_key: Optional[str], parse_mode: str, target_chat_id: Optional[str]):
        """Background worker thread executing network request."""
        try:
            res = self.send_message_sync(text, parse_mode=parse_mode, target_chat_id=target_chat_id)
            status = res.get("status")
            if status == "success":
                print(f"[TELEGRAM SENT] ✅ Successfully dispatched alert ({alert_key or 'Direct'})")
            else:
                print(f"[TELEGRAM WARNING] ⚠️ Dispatch failed or partial: {res}")
        except Exception as e:
            print(f"[TELEGRAM ERROR] ❌ Background sender failed: {e}")

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
        Alert 1: Dispatches instant Telegram notification when BTC or ETH hits EOS/EOR level.
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
            f"🚨 <b>CRYPTO LTP ALERT: NEW TRADE SETUP</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Asset:</b> {asset_name}\n"
            f"📊 <b>Setup Type:</b> {header_icon} <b>{setup_type}</b>\n"
            f"🎯 <b>Entry Level:</b> <b>${entry_price:,.2f}</b>\n"
            f"🏁 <b>Target Level:</b> <b>${target_price:,.2f}</b>\n"
            f"🛡️ <b>Stop Loss (SL):</b> <b>${sl_price:,.2f}</b>\n"
            f"⏰ <b>Trigger Time:</b> {trigger_time} IST\n"
            f"⚡ <b>Action:</b> <code>{action_text}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <b>COA Strategy:</b> Support/Resistance is STRONG.\n"
        )
        if notes:
            message += f"📝 <b>Context:</b> {notes}\n"
        message += f"📱 <i>Ashish Goswami LTP Calculator Pro</i>"

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
        Alert 2: Dispatches instant Telegram exit notification when trade hits Target or SL.
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
            header_line = "🎯 <b>CRYPTO LTP ALERT: TARGET HIT ✅</b> 🎯"
            pts_line = f"📈 <b>Points Captured:</b> <b>+{points:,.2f} pts</b> 💰"
        else:
            header_line = "⚠️ <b>CRYPTO LTP ALERT: STOP LOSS HIT ❌</b> ⚠️"
            pts_line = f"📉 <b>Points Loss:</b> <b>-{abs(points):,.2f} pts</b> 🛡️"

        message = (
            f"{header_line}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>Asset:</b> {asset_name}\n"
            f"📊 <b>Setup:</b> {setup_type}\n"
            f"🏁 <b>Outcome:</b> <b>{outcome}</b>\n"
            f"💵 <b>Exit Price:</b> <b>${exit_price:,.2f}</b>\n"
            f"{pts_line}\n"
            f"⏱️ <b>Trade Duration:</b> {duration_minutes:.1f} min\n"
            f"⏰ <b>Exit Time:</b> {exit_time} IST\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 <i>Ashish Goswami LTP Calculator Pro</i>"
        )

        self.send_message_async(message, alert_key=alert_key)
        return True

    # =========================================================================
    # ON-DEMAND MARKET & TRADE SUMMARIES (FOR TELEGRAM COMMANDS & DASHBOARD)
    # =========================================================================
    def generate_live_market_summary(self, asset: str = "BTC") -> str:
        """
        Generates clean live market snapshot for BTC or ETH with Spot, EOS, EOR, Support/Resistance & PCR.
        """
        symbol = asset.upper()
        if not self.service or not self.calc_engine:
            return f"⚠️ Market service not initialized for {symbol}."

        try:
            records, meta = self.service.fetch_option_chain_snapshot(symbol_prefix=symbol)
            if not records or not meta:
                return f"⚠️ Live data unavailable for {symbol} at this moment. Please try again shortly."

            spot = float(meta.get("spot_price") or 0.0)
            analysis = self.calc_engine.analyze(records, spot, meta.get("expiry_date", ""))

            eos = analysis.get("eos", {})
            eor = analysis.get("eor", {})
            supp = analysis.get("support", {})
            res = analysis.get("resistance", {})
            scenario = analysis.get("coa_scenario", {})
            pcr = analysis.get("pcr", 0.0)
            now_str = get_ist_str()

            asset_name = "Bitcoin (BTC)" if symbol == "BTC" else ("Ethereum (ETH)" if symbol == "ETH" else symbol)
            eos_p = float(eos.get("eos_price") or 0.0)
            eor_p = float(eor.get("eor_price") or 0.0)
            supp_strike = supp.get("primary_strike", "-")
            supp_status = supp.get("overall_status", "STRONG")
            res_strike = res.get("primary_strike", "-")
            res_status = res.get("overall_status", "STRONG")
            scenario_name = scenario.get("name", "COA Analysis")

            active_trades_count = 0
            if self.db:
                try:
                    active_trades = self.db.get_active_trades(asset=symbol)
                    active_trades_count = len(active_trades)
                except Exception:
                    pass

            msg = (
                f"📊 <b>ASHISH GOSWAMI LTP CALCULATOR PRO</b>\n"
                f"🪙 <b>Live Snapshot: {asset_name}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 <b>Live Spot:</b> <b>${spot:,.2f}</b>\n"
                f"🟢 <b>EOS (Support Reversal):</b> <b>${eos_p:,.2f}</b>\n"
                f"🔴 <b>EOR (Resistance Reversal):</b> <b>${eor_p:,.2f}</b>\n"
                f"🛡️ <b>Support:</b> {supp_status} @ Strike <b>{supp_strike}</b>\n"
                f"⛔ <b>Resistance:</b> {res_status} @ Strike <b>{res_strike}</b>\n"
                f"📈 <b>PCR:</b> <b>{pcr:.2f}</b>\n"
                f"🎯 <b>COA Scenario:</b> <code>{scenario_name}</code>\n"
                f"⚡ <b>Active Trades:</b> <b>{active_trades_count} Live</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"⏰ <b>Snapshot Time:</b> {now_str} IST\n"
                f"📱 <i>Delta Exchange Live Feed</i>"
            )
            return msg
        except Exception as e:
            return f"⚠️ Error generating market snapshot for {symbol}: {e}"

    def generate_trade_audit_summary(self) -> str:
        """
        Generates audit and active trades summary report.
        """
        if not self.db:
            return "⚠️ Database not initialized."

        try:
            stats = self.db.get_audit_stats()
            active_trades = self.db.get_active_trades()
            now_str = get_ist_str()

            total = stats.get("total_setups", 0)
            target_hits = stats.get("target_hits", 0)
            sl_hits = stats.get("sl_hits", 0)
            win_rate = stats.get("win_rate_pct", 0.0)
            profit_pts = stats.get("total_profit_points", 0.0)
            loss_pts = stats.get("total_loss_points", 0.0)
            net_pts = round(profit_pts - loss_pts, 2)
            pts_sign = "+" if net_pts >= 0 else ""

            msg = (
                f"📋 <b>TRADE AUDIT & PERFORMANCE SUMMARY</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 <b>Total Setups Triggered:</b> <b>{total}</b>\n"
                f"✅ <b>Target Hits:</b> <b>{target_hits}</b> ({win_rate}% Win Rate)\n"
                f"❌ <b>SL Hits:</b> <b>{sl_hits}</b>\n"
                f"⏳ <b>Active Live Trades:</b> <b>{len(active_trades)}</b>\n"
                f"💰 <b>Net Captured:</b> <b>{pts_sign}{net_pts:,.2f} pts</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
            if active_trades:
                msg += "⚡ <b>Active Trades List:</b>\n"
                for t in active_trades[:5]:
                    a = t.get("asset", "")
                    st = t.get("setup_type", "")
                    entry = t.get("entry_level", 0.0)
                    tgt = t.get("target_price", 0.0)
                    sl = t.get("sl_price", 0.0)
                    msg += f"• <b>{a}</b> {st} @ ${entry:,.2f} (Target: ${tgt:,.2f}, SL: ${sl:,.2f})\n"
                msg += "━━━━━━━━━━━━━━━━━━━━\n"
            else:
                msg += "✨ <i>Abhi koi active trade pending nahi hai.</i>\n━━━━━━━━━━━━━━━━━━━━\n"

            msg += f"⏰ <b>Time:</b> {now_str} IST\n📱 <i>Ashish Goswami LTP Calculator Pro</i>"
            return msg
        except Exception as e:
            return f"⚠️ Error generating trade audit summary: {e}"

    def generate_combined_levels_summary(self) -> str:
        """Generates concise levels summary for both BTC and ETH."""
        btc_summary = self.generate_live_market_summary("BTC")
        eth_summary = self.generate_live_market_summary("ETH")
        return f"{btc_summary}\n\n{eth_summary}"

    def send_live_snapshot(self, asset: str = "BTC", target_chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Sends on-demand live market snapshot to configured chat ID."""
        text = self.generate_live_market_summary(asset=asset)
        return self.send_message_sync(text, target_chat_id=target_chat_id)

    def send_test_alert(self) -> Dict[str, Any]:
        """Sends a verification test Telegram alert to confirm credentials."""
        now_str = get_ist_str()
        message = (
            f"🤖 <b>CRYPTO LTP CALCULATOR PRO - TELEGRAM ALERT TEST</b> ✅\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 <b>Badhai ho!</b> Aapka Telegram Bot Notification Engine safaltapoorvak connect ho gaya hai!\n"
            f"🆔 <b>Chat ID:</b> <code>{self.chat_id}</code>\n"
            f"⏰ <b>Timestamp:</b> {now_str} IST\n"
            f"🪙 <b>Active Monitored Assets:</b> BTC & ETH (Delta Exchange)\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>On-Demand Commands Aap Telegram me bhej sakte hain:</b>\n"
            f"  • <code>/btc</code> ya <code>/status</code> - Bitcoin Live Levels\n"
            f"  • <code>/eth</code> - Ethereum Live Levels\n"
            f"  • <code>/levels</code> - BTC & ETH dono ke levels\n"
            f"  • <code>/trades</code> - Active Trades & Target/SL report\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 <i>Ashish Goswami LTP Calculator Pro</i>"
        )
        return self.send_message_sync(message)

    # =========================================================================
    # INTERACTIVE TELEGRAM BOT LISTENER (RECEIVES COMMANDS FROM TELEGRAM CHAT)
    # =========================================================================
    def start_bot_listener(self):
        """Starts non-blocking background thread listening for user commands in Telegram."""
        if self._listener_running:
            return

        self._listener_running = True
        self._listener_thread = threading.Thread(
            target=self._bot_listener_loop,
            daemon=True,
            name="TelegramBotListener"
        )
        self._listener_thread.start()
        print("[INFO] 🤖 Telegram Bot Interactive Command Listener started!")

    def stop_bot_listener(self):
        """Stops background bot listener gracefully."""
        self._listener_running = False

    def _bot_listener_loop(self):
        """Polls getUpdates from Telegram Bot API and responds to user commands."""
        time.sleep(2)
        while self._listener_running:
            if not self.bot_token or not self.enabled:
                time.sleep(5)
                continue

            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                params = {"timeout": 15}
                if self._last_update_id is not None:
                    params["offset"] = self._last_update_id + 1

                resp = requests.get(url, params=params, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("result", []):
                        self._last_update_id = item.get("update_id")
                        msg = item.get("message", {})
                        text = (msg.get("text") or "").strip().lower()
                        sender_chat_id = str(msg.get("chat", {}).get("id", ""))

                        if not text or not sender_chat_id:
                            continue

                        # Respond to commands
                        self._handle_incoming_command(text, sender_chat_id)

            except Exception as e:
                # Network hiccups or timeouts
                time.sleep(3)

            time.sleep(1)

    def _handle_incoming_command(self, cmd: str, sender_chat_id: str):
        """Processes incoming user command from Telegram chat and sends response."""
        cmd_clean = cmd.split("@")[0].strip()  # Remove bot username if tagged (e.g. /btc@bot)

        if cmd_clean in ["/start", "/help", "help", "hi", "hello"]:
            welcome_text = (
                f"👋 <b>Namaste! Main hoon Ashish Goswami Terminal Bot</b> 🤖\n\n"
                f"Aap kisi bhi samay mujhse live market levels aur swing signals maang sakte hain:\n\n"
                f"🎯 <b>/scan</b> - Nifty 100 Swing Action Screener (Setup C / Supertrend Flip)\n"
                f"🌅 <b>/morning</b> - Daily Morning Market & Swing Digest\n"
                f"👉 <b>/btc</b> ya <b>/status</b> - Bitcoin live spot, EOS, EOR, Support/Resistance\n"
                f"👉 <b>/eth</b> - Ethereum live spot, EOS, EOR, Support/Resistance\n"
                f"👉 <b>/levels</b> - BTC & ETH dono ke live levels ek saath\n"
                f"👉 <b>/trades</b> - Active trades aur Target/SL report\n"
                f"👉 <b>/test</b> - Connection verification test message\n\n"
                f"<i>Stocks ke liye <b>/scan</b> bhejein!</i>"
            )
            self.send_message_async(welcome_text, target_chat_id=sender_chat_id)

        elif cmd_clean in ["/btc", "btc", "/status", "status"]:
            reply = self.generate_live_market_summary("BTC")
            self.send_message_async(reply, target_chat_id=sender_chat_id)

        elif cmd_clean in ["/eth", "eth"]:
            reply = self.generate_live_market_summary("ETH")
            self.send_message_async(reply, target_chat_id=sender_chat_id)

        elif cmd_clean in ["/levels", "levels"]:
            reply = self.generate_combined_levels_summary()
            self.send_message_async(reply, target_chat_id=sender_chat_id)

        elif cmd_clean in ["/trades", "trades", "/audit", "audit"]:
            reply = self.generate_trade_audit_summary()
            self.send_message_async(reply, target_chat_id=sender_chat_id)

        elif cmd_clean in ["/test", "test"]:
            reply = self.send_test_alert()
            # test alert sends to default chat_id, if different send confirmation
            if sender_chat_id != self.chat_id:
                self.send_message_async("✅ Test alert successfully executed!", target_chat_id=sender_chat_id)

        elif cmd_clean in ["/scan", "scan", "/screener", "screener", "/stocks", "stocks"]:
            self.send_message_async("⏳ <i>Scanning Nifty 100 universe for Swing setups (Setup C included)... Please wait 10-15s...</i>", target_chat_id=sender_chat_id)
            try:
                import sys
                stock_path = r"C:\Users\Lenovo\.gemini\antigravity\scratch\stock_scanner"
                if stock_path not in sys.path:
                    sys.path.insert(0, stock_path)
                from backend.app.alerts.telegram_digest import generate_instant_screener_telegram_alert
                report = generate_instant_screener_telegram_alert()
                self.send_message_async(report, target_chat_id=sender_chat_id)
            except Exception as e:
                self.send_message_async(f"⚠️ Error running stock scan: {e}", target_chat_id=sender_chat_id)

        elif cmd_clean in ["/morning", "morning", "/digest", "digest"]:
            self.send_message_async("⏳ <i>Compiling Morning Swing Market Digest...</i>", target_chat_id=sender_chat_id)
            try:
                import sys
                stock_path = r"C:\Users\Lenovo\.gemini\antigravity\scratch\stock_scanner"
                if stock_path not in sys.path:
                    sys.path.insert(0, stock_path)
                from backend.app.alerts.telegram_digest import generate_morning_telegram_digest
                digest = generate_morning_telegram_digest()
                self.send_message_async(digest, target_chat_id=sender_chat_id)
            except Exception as e:
                self.send_message_async(f"⚠️ Error compiling digest: {e}", target_chat_id=sender_chat_id)

        else:
            fallback_text = (
                f"❓ Command samajh nahi aayi: <code>{cmd}</code>\n\n"
                f"Inme se koi command bhejein:\n"
                f"• <b>/scan</b> - 🎯 Nifty 100 Swing Action Screener (Setup C)\n"
                f"• <b>/morning</b> - 🌅 Daily Morning Market Digest\n"
                f"• <b>/btc</b> - Bitcoin Live Levels\n"
                f"• <b>/eth</b> - Ethereum Live Levels\n"
                f"• <b>/levels</b> - BTC & ETH Levels\n"
                f"• <b>/trades</b> - Active Trades Report"
            )
            self.send_message_async(fallback_text, target_chat_id=sender_chat_id)
