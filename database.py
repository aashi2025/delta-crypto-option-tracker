import os
import sqlite3
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

class OptionChainDB:
    def __init__(self, db_path: str = "option_chain_data.db"):
        self._memory_conn: Optional[sqlite3.Connection] = None
        if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
            if not db_path.startswith("/tmp/"):
                db_path = os.path.join("/tmp", os.path.basename(db_path))
            try:
                os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
            except Exception:
                db_path = ":memory:"
        self.db_path = db_path
        try:
            self._init_db()
        except Exception as e:
            print(f"DB init fallback warning: {e}. Falling back to in-memory SQLite.")
            self.db_path = ":memory:"
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._init_db_conn(self._memory_conn)

    def _get_connection(self):
        if self.db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._init_db_conn(self._memory_conn)
            return self._memory_conn
        try:
            db_dir = os.path.dirname(os.path.abspath(self.db_path))
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            return sqlite3.connect(self.db_path, check_same_thread=False)
        except Exception as e:
            print(f"[WARNING] Connecting to disk db {self.db_path} failed: {e}. Falling back to in-memory.")
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._init_db_conn(self._memory_conn)
            return self._memory_conn

    def _init_db_conn(self, conn: sqlite3.Connection):
        """Initializes tables directly on a given connection (for in-memory fallback)."""
        self._execute_schema(conn)

    def _init_db(self):
        """Initialize SQLite database schema and indexes for COA 1.0 Engine."""
        if self.db_path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._init_db_conn(self._memory_conn)
            return
        with self._get_connection() as conn:
            self._execute_schema(conn)

    def _execute_schema(self, conn: sqlite3.Connection):
        cursor = conn.cursor()
        
        # Table 1: Raw Option Chain Records with Diversion prices
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS option_chain_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                instrument_key TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                spot_price REAL,
                strike_price REAL NOT NULL,
                call_ltp REAL,
                call_volume INTEGER,
                call_oi REAL,
                call_oi_change REAL,
                call_bid_price REAL,
                call_bid_qty INTEGER,
                call_ask_price REAL,
                call_ask_qty INTEGER,
                call_iv REAL,
                call_diversion REAL,
                put_ltp REAL,
                put_volume INTEGER,
                put_oi REAL,
                put_oi_change REAL,
                put_bid_price REAL,
                put_bid_qty INTEGER,
                put_ask_price REAL,
                put_ask_qty INTEGER,
                put_iv REAL,
                put_diversion REAL
            )
        """)

        # Table 2: COA 1.0 Analysis Snapshots Summary with UR, US, and SOC
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coa_analysis_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                instrument_key TEXT NOT NULL,
                expiry_date TEXT NOT NULL,
                spot_price REAL,
                resistance_strike REAL,
                resistance_status TEXT,
                res_vol_ratio REAL,
                res_oi_ratio REAL,
                support_strike REAL,
                support_status TEXT,
                supp_vol_ratio REAL,
                supp_oi_ratio REAL,
                coa_scenario INTEGER,
                scenario_name TEXT,
                market_state TEXT,
                eos_price REAL,
                eor_price REAL,
                ur_price REAL,
                us_price REAL,
                soc_flag INTEGER
            )
        """)

        # Table 3: Automated Trade Audit & Performance Tracker Log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trade_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                date TEXT NOT NULL,
                asset TEXT NOT NULL,
                setup_type TEXT NOT NULL,
                trigger_price REAL NOT NULL,
                entry_level REAL NOT NULL,
                target_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                status TEXT NOT NULL,
                exit_time TEXT,
                points_captured REAL DEFAULT 0,
                loss_points REAL DEFAULT 0,
                duration_minutes REAL DEFAULT 0,
                notes TEXT
            )
        """)

        # Indexes for fast querying
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON option_chain_snapshots (timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_id ON option_chain_snapshots (snapshot_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_coa_snapshot ON coa_analysis_snapshots (snapshot_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_date ON trade_audit_log (date);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_status ON trade_audit_log (status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_asset ON trade_audit_log (asset);")

        # Safely add missing columns to existing DB if needed
        for col in ["call_diversion", "put_diversion"]:
            try:
                cursor.execute(f"ALTER TABLE option_chain_snapshots ADD COLUMN {col} REAL;")
            except sqlite3.OperationalError:
                pass

        for col in ["ur_price", "us_price"]:
            try:
                cursor.execute(f"ALTER TABLE coa_analysis_snapshots ADD COLUMN {col} REAL;")
            except sqlite3.OperationalError:
                pass

        try:
            cursor.execute("ALTER TABLE coa_analysis_snapshots ADD COLUMN soc_flag INTEGER;")
        except sqlite3.OperationalError:
            pass

        conn.commit()

    def save_snapshot(self, records: List[Dict[str, Any]], analysis_res: Optional[Dict[str, Any]] = None, csv_path: Optional[str] = None):
        """
        Saves snapshot records and COA 1.0 analysis summary to SQLite database and CSV.
        """
        if not records:
            return

        # 1. Save Raw Records
        columns = [
            "timestamp", "snapshot_id", "instrument_key", "expiry_date", "spot_price", "strike_price",
            "call_ltp", "call_volume", "call_oi", "call_oi_change", "call_bid_price", "call_bid_qty", "call_ask_price", "call_ask_qty", "call_iv", "call_diversion",
            "put_ltp", "put_volume", "put_oi", "put_oi_change", "put_bid_price", "put_bid_qty", "put_ask_price", "put_ask_qty", "put_iv", "put_diversion"
        ]

        query = f"""
            INSERT INTO option_chain_snapshots ({', '.join(columns)})
            VALUES ({', '.join(['?'] * len(columns))})
        """

        data_tuples = []
        for r in records:
            data_tuples.append((
                r.get("timestamp"),
                r.get("snapshot_id"),
                r.get("instrument_key"),
                r.get("expiry_date"),
                r.get("spot_price"),
                r.get("strike_price"),
                r.get("call_ltp"),
                r.get("call_volume"),
                r.get("call_oi"),
                r.get("call_oi_change"),
                r.get("call_bid_price"),
                r.get("call_bid_qty"),
                r.get("call_ask_price"),
                r.get("call_ask_qty"),
                r.get("call_iv"),
                r.get("call_diversion", r.get("strike_price", 0.0) + r.get("call_ltp", 0.0)),
                r.get("put_ltp"),
                r.get("put_volume"),
                r.get("put_oi"),
                r.get("put_oi_change"),
                r.get("put_bid_price"),
                r.get("put_bid_qty"),
                r.get("put_ask_price"),
                r.get("put_ask_qty"),
                r.get("put_iv"),
                r.get("put_diversion", r.get("strike_price", 0.0) - r.get("put_ltp", 0.0))
            ))

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(query, data_tuples)

            # 2. Save Analysis Summary if provided
            if analysis_res:
                res_info = analysis_res.get("resistance", {})
                supp_info = analysis_res.get("support", {})
                coa_info = analysis_res.get("coa_scenario", {})
                eos_info = analysis_res.get("eos", {})
                eor_info = analysis_res.get("eor", {})
                ur_info = analysis_res.get("ur", {})
                us_info = analysis_res.get("us", {})

                first_rec = records[0]
                cursor.execute("""
                    INSERT INTO coa_analysis_snapshots (
                        timestamp, snapshot_id, instrument_key, expiry_date, spot_price,
                        resistance_strike, resistance_status, res_vol_ratio, res_oi_ratio,
                        support_strike, support_status, supp_vol_ratio, supp_oi_ratio,
                        coa_scenario, scenario_name, market_state, eos_price, eor_price,
                        ur_price, us_price, soc_flag
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    first_rec.get("timestamp"),
                    first_rec.get("snapshot_id"),
                    first_rec.get("instrument_key"),
                    first_rec.get("expiry_date"),
                    analysis_res.get("spot_price"),
                    res_info.get("primary_strike"),
                    res_info.get("overall_status"),
                    res_info.get("vol_ratio_pct"),
                    res_info.get("oi_ratio_pct"),
                    supp_info.get("primary_strike"),
                    supp_info.get("overall_status"),
                    supp_info.get("vol_ratio_pct"),
                    supp_info.get("oi_ratio_pct"),
                    coa_info.get("scenario"),
                    coa_info.get("name"),
                    coa_info.get("market_state"),
                    eos_info.get("eos_price"),
                    eor_info.get("eor_price"),
                    ur_info.get("ur_price", eor_info.get("eor_price")),
                    us_info.get("us_price", eos_info.get("eos_price")),
                    1 if analysis_res.get("soc_detected") else 0
                ))

            conn.commit()

        # 3. Append to CSV if requested
        if csv_path:
            df = pd.DataFrame(records)
            file_exists = os.path.exists(csv_path)
            df.to_csv(csv_path, mode='a', header=not file_exists, index=False)

    def list_snapshots(self, limit: int = 50) -> pd.DataFrame:
        """Returns distinct snapshots saved in the database with COA analysis."""
        query = """
            SELECT s.snapshot_id, s.timestamp, s.instrument_key, s.expiry_date, s.spot_price,
                   c.resistance_strike as RES_STRIKE, c.resistance_status as RES_STATUS,
                   c.support_strike as SUPP_STRIKE, c.support_status as SUPP_STATUS,
                   c.coa_scenario as COA_SCENARIO, c.market_state as MARKET_STATE,
                   c.eos_price as EOS, c.eor_price as EOR, c.ur_price as UR, c.us_price as US, c.soc_flag as SOC
            FROM option_chain_snapshots s
            LEFT JOIN coa_analysis_snapshots c ON s.snapshot_id = c.snapshot_id
            GROUP BY s.snapshot_id
            ORDER BY s.timestamp DESC
            LIMIT ?
        """
        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=(limit,))

    def get_snapshot(self, snapshot_id: str) -> pd.DataFrame:
        """Fetch all strikes for a specific snapshot_id."""
        query = """
            SELECT * FROM option_chain_snapshots
            WHERE snapshot_id = ?
            ORDER BY strike_price ASC
        """
        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=(snapshot_id,))

    def get_snapshot_analysis(self, snapshot_id: str) -> pd.DataFrame:
        """Fetch COA analysis summary for a specific snapshot_id."""
        query = """
            SELECT * FROM coa_analysis_snapshots
            WHERE snapshot_id = ?
        """
        with self._get_connection() as conn:
            return pd.read_sql_query(query, conn, params=(snapshot_id,))

    def get_candles(self, instrument_key: str = "BTC", limit: int = 100) -> List[Dict[str, Any]]:
        """
        Fetches historical spot prices aggregated into 1-minute OHLC candles for TradingView Lightweight Charts.
        """
        query = """
            SELECT timestamp, spot_price 
            FROM option_chain_snapshots 
            WHERE instrument_key = ? AND spot_price IS NOT NULL AND spot_price > 0
            ORDER BY id ASC
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (instrument_key,))
            rows = cursor.fetchall()
            
            if not rows:
                cursor.execute("""
                    SELECT timestamp, spot_price 
                    FROM coa_analysis_snapshots 
                    WHERE instrument_key = ? AND spot_price IS NOT NULL AND spot_price > 0
                    ORDER BY id ASC
                """, (instrument_key,))
                rows = cursor.fetchall()

        if not rows:
            return []

        grouped = {}
        for ts_str, spot in rows:
            minute_key = ts_str[:16] + ":00" if len(ts_str) >= 16 else ts_str
            if minute_key not in grouped:
                grouped[minute_key] = []
            grouped[minute_key].append(spot)

        candles = []
        for minute_key, prices in grouped.items():
            try:
                dt = datetime.strptime(minute_key, "%Y-%m-%d %H:%M:%S")
                timestamp_unix = int(dt.timestamp())
                candles.append({
                    "time": timestamp_unix,
                    "open": prices[0],
                    "high": max(prices),
                    "low": min(prices),
                    "close": prices[-1]
                })
            except Exception:
                continue

        candles.sort(key=lambda x: x["time"])
        return candles[-limit:]

    def get_920_snapshot(self, instrument_key: str = "BTC", target_date: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """
        Strictly queries SQLite database for 09:20:00 AM IST snapshot for target date.
        Does NOT fallback to live current time.
        """
        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT snapshot_id, timestamp, spot_price, expiry_date
                FROM option_chain_snapshots
                WHERE instrument_key = ?
                  AND timestamp LIKE ?
                  AND strftime('%H:%M:%S', timestamp) >= '09:18:00'
                  AND strftime('%H:%M:%S', timestamp) <= '09:25:00'
                ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ? || ' 09:20:00')) ASC
                LIMIT 1
            """
            cursor.execute(query, (instrument_key, f"{target_date}%", target_date))
            row = cursor.fetchone()

            if not row:
                cursor.execute("""
                    SELECT snapshot_id, timestamp, spot_price, expiry_date
                    FROM option_chain_snapshots
                    WHERE instrument_key = ?
                      AND timestamp LIKE ?
                      AND strftime('%H:%M:%S', timestamp) >= '09:15:00'
                      AND strftime('%H:%M:%S', timestamp) <= '09:30:00'
                    ORDER BY timestamp ASC
                    LIMIT 1
                """, (instrument_key, f"{target_date}%"))
                row = cursor.fetchone()

            if not row:
                return [], None

            snapshot_id, ts, spot, expiry = row
            df_records = self.get_snapshot(snapshot_id)
            if df_records.empty:
                return [], None

            records = df_records.to_dict(orient="records")
            meta = {
                "timestamp": ts,
                "snapshot_id": snapshot_id,
                "spot_price": spot,
                "expiry_date": expiry,
                "instrument_key": instrument_key
            }
            return records, meta

    def insert_trade_audit(
        self,
        timestamp: str,
        asset: str,
        setup_type: str,
        trigger_price: float,
        entry_level: float,
        target_price: float,
        sl_price: float,
        status: str = "ACTIVE",
        notes: str = ""
    ) -> int:
        """Inserts a new trade setup record into trade_audit_log."""
        date_str = timestamp.split(" ")[0] if " " in timestamp else datetime.now().strftime("%Y-%m-%d")
        query = """
            INSERT INTO trade_audit_log (
                timestamp, date, asset, setup_type, trigger_price, entry_level,
                target_price, sl_price, status, points_captured, loss_points, duration_minutes, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 0.0, 0.0, ?)
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (
                timestamp, date_str, asset.upper(), setup_type,
                trigger_price, entry_level, target_price, sl_price, status, notes
            ))
            conn.commit()
            return cursor.lastrowid

    def update_trade_outcome(
        self,
        trade_id: int,
        status: str,
        exit_time: str,
        points_captured: float = 0.0,
        loss_points: float = 0.0,
        duration_minutes: float = 0.0,
        notes: Optional[str] = None
    ):
        """Updates trade status to 'TARGET HIT ✅' or 'SL HIT ❌' with exit details."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if notes is not None:
                cursor.execute("""
                    UPDATE trade_audit_log
                    SET status = ?, exit_time = ?, points_captured = ?, loss_points = ?, duration_minutes = ?, notes = ?
                    WHERE id = ?
                """, (status, exit_time, points_captured, loss_points, duration_minutes, notes, trade_id))
            else:
                cursor.execute("""
                    UPDATE trade_audit_log
                    SET status = ?, exit_time = ?, points_captured = ?, loss_points = ?, duration_minutes = ?
                    WHERE id = ?
                """, (status, exit_time, points_captured, loss_points, duration_minutes, trade_id))
            conn.commit()

    def get_active_trades(self, asset: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetches all currently ACTIVE trades."""
        query = "SELECT * FROM trade_audit_log WHERE status = 'ACTIVE'"
        params = []
        if asset and asset.upper() != "ALL":
            query += " AND asset = ?"
            params.append(asset.upper())
        query += " ORDER BY id ASC"

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_last_trade_for_setup(self, asset: str, setup_type: str) -> Optional[Dict[str, Any]]:
        """Fetches most recent trade for this asset & setup_type for debounce/cooldown check."""
        query = """
            SELECT * FROM trade_audit_log
            WHERE asset = ? AND setup_type = ?
            ORDER BY id DESC
            LIMIT 1
        """
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, (asset.upper(), setup_type))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_trade_audit_log(self, date_filter: Optional[str] = None, asset: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Fetches trade audit log records with optional date and asset filters."""
        query = "SELECT * FROM trade_audit_log WHERE 1=1"
        params = []
        if date_filter and date_filter.upper() != "ALL":
            query += " AND date = ?"
            params.append(date_filter)
        if asset and asset.upper() != "ALL":
            query += " AND asset = ?"
            params.append(asset.upper())
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_audit_dates(self) -> List[str]:
        """Returns sorted list of distinct dates present in trade audit log."""
        query = "SELECT DISTINCT date FROM trade_audit_log ORDER BY date DESC"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            return [r[0] for r in rows if r[0]]

    def get_audit_stats(self, date_filter: Optional[str] = None, asset: Optional[str] = None) -> Dict[str, Any]:
        """Calculates Top Summary Counters (Total Setups, Target Hits, SL Hits, Active, Success Rate %)."""
        query = "SELECT status, points_captured, loss_points FROM trade_audit_log WHERE 1=1"
        params = []
        if date_filter and date_filter.upper() != "ALL":
            query += " AND date = ?"
            params.append(date_filter)
        if asset and asset.upper() != "ALL":
            query += " AND asset = ?"
            params.append(asset.upper())

        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

        total = len(rows)
        target_hits = sum(1 for r in rows if "TARGET HIT" in r["status"])
        sl_hits = sum(1 for r in rows if "SL HIT" in r["status"])
        active = sum(1 for r in rows if r["status"] == "ACTIVE")
        closed = target_hits + sl_hits
        win_rate = round((target_hits / closed * 100.0), 1) if closed > 0 else 0.0
        total_profit_pts = round(sum(r["points_captured"] or 0.0 for r in rows), 2)
        total_loss_pts = round(sum(r["loss_points"] or 0.0 for r in rows), 2)

        return {
            "total_setups": total,
            "target_hits": target_hits,
            "sl_hits": sl_hits,
            "active_trades": active,
            "win_rate_pct": win_rate,
            "total_profit_points": total_profit_pts,
            "total_loss_points": total_loss_pts
        }



