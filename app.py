import os
import json
import time
import asyncio
import sqlite3
import random
import uuid

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from dotenv import load_dotenv

import websockets


# =========================================================
# CONFIGURACIÓN
# =========================================================

load_dotenv()

BASE = Path(__file__).parent
DB = Path(os.getenv("DB_PATH", str(BASE / "pumpcopilot.db")))

API_KEY = os.getenv(
    "PUMPPORTAL_API_KEY",
    ""
)

APP_TOKEN = os.getenv(
    "APP_TOKEN",
    "change-this-long-random-token"
)

WATCHED = json.loads(
    os.getenv(
        "WATCHED_WALLETS",
        "{}"
    )
)

WINDOW = int(
    os.getenv(
        "CONSENSUS_WINDOW_SECONDS",
        "90"
    )
)

PAPER_BUY_USD = float(
    os.getenv(
        "PAPER_BUY_USD",
        "5"
    )
)




app = FastAPI(
    title="Pump Copilot V3"
)


# =========================================================
# PESO INICIAL DE LOS TRADERS
# =========================================================
#
# Estos valores NO significan que ya sepamos
# quién es mejor.
#
# Son valores iniciales.
# Después Pump Copilot los irá ajustando con
# resultados reales / paper trading.
#
# Máximo: 30 puntos
# =========================================================

TRADER_QUALITY = {
    "marcell": 25,
    "hdegroot": 27,
    "gr3gor14n": 24,
    "epicsealdarkeye": 18,
    "supermandev": 18
}
TRACKED_TOKENS = set()
SUBSCRIBED_TOKENS = set()
TOKENS_TO_UNSUBSCRIBE = set()

# Evita procesar dos veces la misma transacción de PumpPortal
KILL_SWITCH = False

DEBUG_MODE = os.getenv(
    "DEBUG_MODE",
    "false"
).lower() == "true"

LIVE_TRADING = os.getenv(
    "LIVE_TRADING",
    "false"
).lower() == "true"

MAX_POSITION_USD = 5.0
MAX_DAILY_LOSS_USD = 5.0
MAX_SLIPPAGE_PCT = 5.0
MIN_LIQUIDITY_SOL = 10.0

EXECUTION_TIMEOUT_SECONDS = 10
MAX_EXECUTION_RETRIES = 2

SEEN_SIGNATURES = set()
FORCE_STREAM_ERROR = False


# =========================================================
# BASE DE DATOS
# =========================================================

def db():

    conn = sqlite3.connect(DB)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ts REAL,

            trader TEXT,

            wallet TEXT,

            side TEXT,

            mint TEXT,

            sol REAL,

            market_cap_sol REAL,

            signature TEXT,

            source TEXT DEFAULT 'live',

            token_amount REAL DEFAULT 0,

            new_token_balance REAL DEFAULT 0,

            pool TEXT DEFAULT ''

        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_signatures(
            signature TEXT PRIMARY KEY,
            ts REAL,
            source TEXT DEFAULT 'live'
        )
        """
    )

    conn.execute(
    """
    CREATE TABLE IF NOT EXISTS app_state(
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """
)


    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluations(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            trade_signature TEXT UNIQUE,

            ts REAL,

            trader TEXT,

            mint TEXT,

            source TEXT,

            score INTEGER,

            decision TEXT,

            trader_score INTEGER,

            timing_score INTEGER,

            size_score INTEGER,

            token_score INTEGER,

            consensus_score INTEGER,

            market_score INTEGER,

            reasons TEXT

        )
        """
    )


    conn.execute(
    """
    CREATE TABLE IF NOT EXISTS paper_positions(

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        opened_ts REAL,

        mint TEXT,

        trigger_traders TEXT,

        entry_mc REAL,

        stake_usd REAL,

        status TEXT,

        pnl_usd REAL DEFAULT 0,

        decision TEXT DEFAULT '',

        score INTEGER DEFAULT 0,

        mode TEXT DEFAULT 'paper'

    )
    """
)
    try:
        conn.execute(
        """
        ALTER TABLE paper_positions
        ADD COLUMN mode TEXT DEFAULT 'paper'
        """
    )
    except sqlite3.OperationalError:
     pass


    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_history(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ts REAL,

            mint TEXT,

            market_cap_sol REAL,

            trader TEXT DEFAULT '',

            side TEXT DEFAULT '',

            signature TEXT DEFAULT '',

            source TEXT DEFAULT 'live'

        )
        """
    )


    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_token_history_mint_ts
        ON token_history(mint, ts)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS position_events(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ts REAL,

            mint TEXT,

            event_type TEXT,

            market_cap_sol REAL,

            remaining_pct REAL,

            realized_pnl_usd REAL,

            details TEXT DEFAULT ''

        )
        """
    )


    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_position_events_mint_ts
        ON position_events(mint, ts)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_created REAL,
            ts_updated REAL,
            mint TEXT,
            side TEXT,
            amount_usd REAL,
            expected_price REAL,
            execution_price REAL,
            liquidity_sol REAL,
            status TEXT,
            reason TEXT DEFAULT '',
            source TEXT DEFAULT 'paper',
            retry_count INTEGER DEFAULT 0,
            parent_order_id INTEGER DEFAULT NULL
        )
        """
    )

    try:
        conn.execute(
        """
        ALTER TABLE execution_orders
        ADD COLUMN mode TEXT DEFAULT 'paper'
        """
    )
    except sqlite3.OperationalError:
     pass

    


    try:
        conn.execute(
        """
        ALTER TABLE execution_orders
        ADD COLUMN parent_order_id INTEGER DEFAULT NULL
        """
    )
    except sqlite3.OperationalError:
     pass


    try:
        conn.execute(
            """
            ALTER TABLE execution_orders
            ADD COLUMN retry_count INTEGER DEFAULT 0
            """
        )
    except sqlite3.OperationalError:
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_order_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            ts REAL,
            status TEXT,
            reason TEXT DEFAULT ''
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_execution_order_events_order_ts
        ON execution_order_events(order_id, ts)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_idempotency(
            idempotency_key TEXT PRIMARY KEY,
            order_id INTEGER,
            ts REAL
        )
        """
    )


    conn.commit()

    return conn



def mark_signature_processed(
    signature,
    source="live"
):

    if not signature:
        return False

    conn = db()

    exists = conn.execute(
        """
        SELECT signature
        FROM processed_signatures
        WHERE signature = ?
        LIMIT 1
        """,
        (
            signature,
        )
    ).fetchone()

    if exists:
        conn.close()
        return False

    conn.execute(
        """
        INSERT INTO processed_signatures(
            signature,
            ts,
            source
        )
        VALUES(
            ?,?,?
        )
        """,
        (
            signature,
            time.time(),
            source
        )
    )

    conn.commit()
    conn.close()

    return True

def count_open_positions(
    mode="paper"
):

    conn = db()

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM paper_positions
        WHERE status = 'open'
        AND mode = ?
        """,
        (
            mode,
        )
    ).fetchone()

    conn.close()

    return int(row[0] or 0)


def get_daily_realized_pnl(
    mode="paper"
):

    now = time.localtime()

    start_of_day = time.mktime(
        (
            now.tm_year,
            now.tm_mon,
            now.tm_mday,
            0,
            0,
            0,
            now.tm_wday,
            now.tm_yday,
            now.tm_isdst
        )
    )

    conn = db()

    row = conn.execute(
        """
        SELECT COALESCE(
            SUM(realized_pnl_usd),
            0
        )

        FROM paper_positions

        WHERE status = 'closed'
        AND closed_ts >= ?
        AND mode = ?
        """,
        (
            start_of_day,
            mode
        )
    ).fetchone()

    conn.close()

    return float(row[0] or 0)


def validate_slippage(
    expected_price,
    execution_price
):

    if expected_price <= 0 or execution_price <= 0:
        return False

    slippage_pct = (
        (execution_price - expected_price)
        / expected_price
    ) * 100

    if slippage_pct > MAX_SLIPPAGE_PCT:
        print(
            f"[RISK BLOCK] Slippage {slippage_pct:.2f}% "
            f"supera máximo {MAX_SLIPPAGE_PCT:.2f}%"
        )
        return False

    return True

def validate_liquidity(
    liquidity_sol
):

    liquidity_sol = float(
        liquidity_sol or 0
    )

    if liquidity_sol < MIN_LIQUIDITY_SOL:
        print(
            f"[RISK BLOCK] Liquidez {liquidity_sol:.2f} SOL "
            f"menor al mínimo {MIN_LIQUIDITY_SOL:.2f} SOL"
        )
        return False

    return True

def simulate_execution(
    mint,
    expected_price,
    execution_price,
    liquidity_sol,
    amount_usd,
    force_fail=False,
    idempotency_key=None,
    parent_order_id=None,
    mode="paper"
):

    if idempotency_key:

        idempotent_order = create_execution_order_idempotent(
            mint=mint,
            side="buy",
            amount_usd=amount_usd,
            expected_price=expected_price,
            execution_price=execution_price,
            liquidity_sol=liquidity_sol,
            idempotency_key=idempotency_key,
            source="paper",
            parent_order_id=parent_order_id,
            mode=mode
        )

        order_id = idempotent_order["order_id"]

        if not idempotent_order["created"]:

            existing_status = get_execution_order_status(
                order_id
            )

            status = existing_status["status"]

            return {
                "ok": status == "CONFIRMED",
                "order_id": order_id,
                "reason": "IDEMPOTENT_REUSE",
                "status": status,
                "original_reason": existing_status["reason"]
            }

    else:

        order_id = create_execution_order(
            mint=mint,
            side="buy",
            amount_usd=amount_usd,
            expected_price=expected_price,
            execution_price=execution_price,
            liquidity_sol=liquidity_sol,
            source="paper",
            parent_order_id=parent_order_id,
            mode=mode
        )

    risk = risk_check(
    mint=mint,
    amount_usd=amount_usd,
    expected_price=expected_price,
    execution_price=execution_price,
    liquidity_sol=liquidity_sol,
    mode=mode
)

    if not risk["ok"]:

        update_execution_order(
            order_id=order_id,
            status="RISK_BLOCKED",
            reason=risk["reason"]
        )

        return {
            "ok": False,
            "order_id": order_id,
            "reason": risk["reason"]
        }

    update_execution_order(
        order_id=order_id,
        status="RISK_CHECKED",
        reason="RISK_OK"
    )

    update_execution_order(
        order_id=order_id,
        status="SENT",
        reason=""
    )

    if force_fail:

        update_execution_order(
            order_id=order_id,
            status="FAILED",
            reason="SIMULATED_EXECUTION_FAILURE"
        )

        return {
            "ok": False,
            "order_id": order_id,
            "reason": "SIMULATED_EXECUTION_FAILURE"
        }

    update_execution_order(
        order_id=order_id,
        status="CONFIRMED",
        reason=""
    )

    return {
        "ok": True,
        "order_id": order_id,
        "reason": "EXECUTION_CONFIRMED",
        "mint": mint,
        "amount_usd": amount_usd,
        "expected_price": expected_price,
        "execution_price": execution_price,
        "liquidity_sol": liquidity_sol
    }


def create_execution_order(
    mint,
    side,
    amount_usd,
    expected_price,
    execution_price,
    liquidity_sol,
    source="paper",
    parent_order_id=None,
    mode="paper"
):

    now = time.time()

    conn = db()

    cursor = conn.execute(
    """
    INSERT INTO execution_orders(
        ts_created,
        ts_updated,
        mint,
        side,
        amount_usd,
        expected_price,
        execution_price,
        liquidity_sol,
        status,
        reason,
        source,
        parent_order_id,
        mode
    )

    VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?
    )
    """,
    (
        now,
        now,
        mint,
        side,
        float(amount_usd or 0),
        float(expected_price or 0),
        float(execution_price or 0),
        float(liquidity_sol or 0),
        "CREATED",
        "",
        source,
        parent_order_id,
        mode
    )
)



    order_id = cursor.lastrowid

    conn.commit()
    conn.close()

    save_execution_order_event(
        order_id=order_id,
        status="CREATED",
        reason=""
    )

    return order_id

def create_execution_order_idempotent(
    mint,
    side,
    amount_usd,
    expected_price,
    execution_price,
    liquidity_sol,
    idempotency_key,
    source="paper",
    parent_order_id=None,
    mode="paper"
):

    conn = db()

    try:

        conn.execute("BEGIN IMMEDIATE")

        existing = conn.execute(
            """
            SELECT order_id
            FROM execution_idempotency
            WHERE idempotency_key = ?
            LIMIT 1
            """,
            (
                idempotency_key,
            )
        ).fetchone()

        if existing:

            order_id = int(existing[0])

            conn.commit()
            conn.close()

            return {
                "created": False,
                "order_id": order_id
            }

        now = time.time()

        cursor = conn.execute(
    """
    INSERT INTO execution_orders(
        ts_created,
        ts_updated,
        mint,
        side,
        amount_usd,
        expected_price,
        execution_price,
        liquidity_sol,
        status,
        reason,
        source,
        parent_order_id,
        mode
    )
    VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?
    )
    """,
    (
        now,
        now,
        mint,
        side,
        float(amount_usd or 0),
        float(expected_price or 0),
        float(execution_price or 0),
        float(liquidity_sol or 0),
        "CREATED",
        "",
        source,
        parent_order_id,
        mode
    )
)

        order_id = cursor.lastrowid

        conn.execute(
            """
            INSERT INTO execution_idempotency(
                idempotency_key,
                order_id,
                ts
            )
            VALUES(?,?,?)
            """,
            (
                idempotency_key,
                order_id,
                now
            )
        )

        conn.commit()
        conn.close()

        save_execution_order_event(
            order_id=order_id,
            status="CREATED",
            reason=""
        )

        return {
            "created": True,
            "order_id": order_id
        }

    except Exception:

        conn.rollback()
        conn.close()

        raise


def update_execution_order(
    order_id,
    status,
    reason=""
):

    conn = db()

    conn.execute(
        """
        UPDATE execution_orders

        SET
            ts_updated = ?,
            status = ?,
            reason = ?

        WHERE id = ?
        """,
        (
            time.time(),
            status,
            reason,
            order_id
        )
    )

    conn.commit()
    conn.close()

    save_execution_order_event(
        order_id=order_id,
        status=status,
        reason=reason
    )

def save_execution_order_event(
    order_id,
    status,
    reason=""
):

    conn = db()

    conn.execute(
        """
        INSERT INTO execution_order_events(
            order_id,
            ts,
            status,
            reason
        )

        VALUES(
            ?,?,?,?
        )
        """,
        (
            order_id,
            time.time(),
            status,
            reason
        )
    )

    conn.commit()
    conn.close()

def get_or_create_idempotency(
    idempotency_key,
    order_id=None
):

    if not idempotency_key:
        return {
            "exists": False,
            "order_id": order_id
        }

    conn = db()

    row = conn.execute(
        """
        SELECT order_id
        FROM execution_idempotency
        WHERE idempotency_key = ?
        LIMIT 1
        """,
        (
            idempotency_key,
        )
    ).fetchone()

    if row:
        conn.close()

        return {
            "exists": True,
            "order_id": int(row[0])
        }

    if order_id is not None:

        conn.execute(
            """
            INSERT INTO execution_idempotency(
                idempotency_key,
                order_id,
                ts
            )
            VALUES(
                ?,?,?
            )
            """,
            (
                idempotency_key,
                order_id,
                time.time()
            )
        )

        conn.commit()

    conn.close()

    return {
        "exists": False,
        "order_id": order_id
    }


def get_execution_order_status(
    order_id
):

    conn = db()

    row = conn.execute(
        """
        SELECT
            status,
            reason

        FROM execution_orders

        WHERE id = ?

        LIMIT 1
        """,
        (
            order_id,
        )
    ).fetchone()

    conn.close()

    if not row:
        return {
            "status": "UNKNOWN",
            "reason": "ORDER_NOT_FOUND"
        }

    return {
        "status": str(row[0] or ""),
        "reason": str(row[1] or "")
    }


def check_execution_timeout(order_id):

    conn = db()

    row = conn.execute(
        """
        SELECT
            status,
            ts_updated

        FROM execution_orders

        WHERE id = ?

        LIMIT 1
        """,
        (
            order_id,
        )
    ).fetchone()

    conn.close()

    if not row:
        return {
            "ok": False,
            "reason": "ORDER_NOT_FOUND"
        }

    status = str(row[0] or "")
    ts_updated = float(row[1] or 0)

    if status != "SENT":
        return {
            "ok": False,
            "reason": "ORDER_NOT_SENT",
            "status": status
        }

    elapsed = time.time() - ts_updated

    return {
        "ok": True,
        "order_id": order_id,
        "status": status,
        "elapsed_seconds": round(elapsed, 3),
        "timed_out": elapsed >= EXECUTION_TIMEOUT_SECONDS
    }

def mark_order_pending_reconciliation(order_id):

    current = get_execution_order_status(order_id)

    if current["status"] not in (
    "SENT",
    "PENDING_RECONCILIATION"
):
        return {
        "ok": False,
        "reason": "ORDER_NOT_PENDING",
        "status": current["status"]
    }

    timeout = check_execution_timeout(order_id)

    if not timeout.get("timed_out"):
        return {
            "ok": False,
            "reason": "ORDER_NOT_TIMED_OUT",
            "status": current["status"]
        }

    update_execution_order(
        order_id,
        "PENDING_RECONCILIATION",
        "EXECUTION_TIMEOUT"
    )

    return {
        "ok": True,
        "order_id": order_id,
        "status": "PENDING_RECONCILIATION",
        "reason": "EXECUTION_TIMEOUT"
    }

def can_retry_execution(order_id):

    conn = db()

    row = conn.execute(
        """
        SELECT
            status,
            reason

        FROM execution_orders

        WHERE id = ?

        LIMIT 1
        """,
        (
            order_id,
        )
    ).fetchone()

    conn.close()

    if not row:
        return {
            "ok": False,
            "reason": "ORDER_NOT_FOUND"
        }

    status = str(row[0] or "")
    reason = str(row[1] or "")

    if status != "FAILED":
        return {
            "ok": False,
            "reason": "ORDER_NOT_FAILED",
            "status": status
        }

    if reason in (
        "SLIPPAGE",
        "LIQUIDITY",
        "MAX_POSITION_USD",
        "MAX_DAILY_LOSS",
        "MAX_OPEN_POSITIONS",
        "KILL_SWITCH"
    ):
        return {
            "ok": False,
            "reason": "NON_RETRYABLE_FAILURE",
            "status": status,
            "original_reason": reason
        }

    return {
        "ok": True,
        "reason": "RETRY_ALLOWED",
        "status": status,
        "original_reason": reason
    }

def increment_execution_retry(order_id):

    conn = db()

    row = conn.execute(
        """
        SELECT retry_count
        FROM execution_orders
        WHERE id = ?
        LIMIT 1
        """,
        (
            order_id,
        )
    ).fetchone()

    if not row:
        conn.close()

        return {
            "ok": False,
            "reason": "ORDER_NOT_FOUND"
        }

    current_retry_count = int(row[0] or 0)

    if current_retry_count >= MAX_EXECUTION_RETRIES:
        conn.close()

        return {
            "ok": False,
            "reason": "MAX_RETRIES_REACHED",
            "retry_count": current_retry_count
        }

    new_retry_count = current_retry_count + 1

    conn.execute(
        """
        UPDATE execution_orders
        SET
            retry_count = ?,
            ts_updated = ?
        WHERE id = ?
        """,
        (
            new_retry_count,
            time.time(),
            order_id
        )
    )

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "order_id": order_id,
        "retry_count": new_retry_count,
        "remaining_retries": MAX_EXECUTION_RETRIES - new_retry_count
    }

def retry_execution_order(
    order_id,
    expected_price,
    execution_price,
    liquidity_sol,
    amount_usd,
    force_fail=False
):

    retry_check = can_retry_execution(
        order_id
    )

    if not retry_check["ok"]:
        return {
            "ok": False,
            "reason": retry_check["reason"]
        }

    retry_counter = increment_execution_retry(
        order_id
    )

    if not retry_counter["ok"]:
        return {
            "ok": False,
            "reason": retry_counter["reason"]
        }

    conn = db()

    row = conn.execute(
        """
        SELECT
    mint,
    side,
    mode
FROM execution_orders
WHERE id = ?
        """,
        (
            order_id,
        )
    ).fetchone()

    conn.close()

    if not row:
        return {
            "ok": False,
            "reason": "ORDER_NOT_FOUND"
        }

    mint = str(row[0] or "")
    side = str(row[1] or "buy")
    mode = str(row[2] or "standard")
    mode = str(row[2] or "paper")

    idempotency_key = (
        f"RETRY-{order_id}-{retry_counter['retry_count']}"
    )

    retry_result = simulate_execution(
        mint=mint,
        expected_price=expected_price,
        execution_price=execution_price,
        liquidity_sol=liquidity_sol,
        amount_usd=amount_usd,
        force_fail=force_fail,
        idempotency_key=idempotency_key,
        parent_order_id=order_id,
        mode=mode
    )

    return {
        "ok": retry_result["ok"],
        "original_order_id": order_id,
        "retry_count": retry_counter["retry_count"],
        "retry_result": retry_result
    }

def get_execution_latency(
    order_id
):

    conn = db()

    rows = conn.execute(
        """
        SELECT
            ts,
            status

        FROM execution_order_events

        WHERE order_id = ?

        ORDER BY ts ASC
        """,
        (
            order_id,
        )
    ).fetchall()

    conn.close()

    times = {
        status: float(ts)
        for ts, status in rows
    }

    created = times.get("CREATED")
    risk_checked = times.get("RISK_CHECKED")
    sent = times.get("SENT")
    confirmed = times.get("CONFIRMED")

    def ms_between(
        start,
        end
    ):
        if start is None or end is None:
            return None

        return round(
            (end - start) * 1000,
            3
        )

    return {
        "order_id": order_id,
        "created_to_risk_ms": ms_between(
            created,
            risk_checked
        ),
        "risk_to_sent_ms": ms_between(
            risk_checked,
            sent
        ),
        "sent_to_confirmed_ms": ms_between(
            sent,
            confirmed
        ),
        "total_ms": ms_between(
            created,
            confirmed
        )
    }


def reconcile_execution_order(
    order_id,
    final_status,
    reason=""
):

    allowed_statuses = (
        "CONFIRMED",
        "FAILED"
    )

    if final_status not in allowed_statuses:
        return {
            "ok": False,
            "reason": "INVALID_FINAL_STATUS"
        }

    current = get_execution_order_status(order_id)

    if current["status"] == "UNKNOWN":
        return {
            "ok": False,
            "reason": "ORDER_NOT_FOUND"
        }

    if current["status"] not in (
    "SENT",
    "PENDING_RECONCILIATION"
):
        return {
        "ok": False,
        "reason": "ORDER_NOT_PENDING",
        "status": current["status"]
    }

    update_execution_order(
        order_id,
        final_status,
        reason
    )

    return {
        "ok": final_status == "CONFIRMED",
        "order_id": order_id,
        "status": final_status,
        "reason": reason
    }

def risk_check(
    mint,
    amount_usd,
    expected_price=None,
    execution_price=None,
    liquidity_sol=None,
    mode="paper"
):

    if KILL_SWITCH:
        return {
            "ok": False,
            "reason": "KILL_SWITCH"
        }

    if amount_usd > MAX_POSITION_USD:
        return {
            "ok": False,
            "reason": "MAX_POSITION_USD"
        }

    daily_pnl = get_daily_realized_pnl(
    mode=mode
)

    if daily_pnl <= -MAX_DAILY_LOSS_USD:
        return {
            "ok": False,
            "reason": "MAX_DAILY_LOSS"
        }

    if (
        expected_price is not None
        and execution_price is not None
    ):

        if not validate_slippage(
            expected_price,
            execution_price
        ):
            return {
                "ok": False,
                "reason": "SLIPPAGE"
            }

    if liquidity_sol is not None:

        if not validate_liquidity(
            liquidity_sol
        ):
            return {
                "ok": False,
                "reason": "LIQUIDITY"
            }

    if count_open_positions(mode=mode) >= 1:
        return {
            "ok": False,
            "reason": "MAX_OPEN_POSITIONS"
        }

    return {
        "ok": True,
        "reason": "RISK_OK",
        "mint": mint
    }


# =========================================================
# MIGRACIÓN DE LA BD VIEJA
# =========================================================

def migrate_database():

    conn = db()

    existing = [
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()
    ]


    migrations = {

        "source":
            "ALTER TABLE trades "
            "ADD COLUMN source TEXT DEFAULT 'live'",

        "token_amount":
            "ALTER TABLE trades "
            "ADD COLUMN token_amount REAL DEFAULT 0",

        "new_token_balance":
            "ALTER TABLE trades "
            "ADD COLUMN new_token_balance REAL DEFAULT 0",

        "pool":
            "ALTER TABLE trades "
            "ADD COLUMN pool TEXT DEFAULT ''"
    }


    for column, sql in migrations.items():

        if column not in existing:

            try:
                conn.execute(sql)

            except Exception:
                pass


    existing_paper = [
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(paper_positions)"
        ).fetchall()
    ]


    if "decision" not in existing_paper:

        try:
            conn.execute(
                """
                ALTER TABLE paper_positions
                ADD COLUMN decision TEXT DEFAULT ''
                """
            )

        except Exception:
            pass


    if "score" not in existing_paper:

        try:
            conn.execute(
                """
                ALTER TABLE paper_positions
                ADD COLUMN score INTEGER DEFAULT 0
                """
            )

        except Exception:
            pass

    existing_paper = [
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(paper_positions)"
        ).fetchall()
    ]

    paper_migrations = {
        "origin_trader":
            "ALTER TABLE paper_positions ADD COLUMN origin_trader TEXT DEFAULT ''",

        "current_mc":
            "ALTER TABLE paper_positions ADD COLUMN current_mc REAL DEFAULT 0",

        "remaining_pct":
            "ALTER TABLE paper_positions ADD COLUMN remaining_pct REAL DEFAULT 1",

        "realized_pnl_usd":
            "ALTER TABLE paper_positions ADD COLUMN realized_pnl_usd REAL DEFAULT 0",

        "unrealized_pnl_usd":
            "ALTER TABLE paper_positions ADD COLUMN unrealized_pnl_usd REAL DEFAULT 0",

        "last_action":
            "ALTER TABLE paper_positions ADD COLUMN last_action TEXT DEFAULT 'HOLD'",

        "tp_stage":
            "ALTER TABLE paper_positions ADD COLUMN tp_stage INTEGER DEFAULT 0",

        "closed_ts":
            "ALTER TABLE paper_positions ADD COLUMN closed_ts REAL DEFAULT 0",

        "exit_mc":
            "ALTER TABLE paper_positions ADD COLUMN exit_mc REAL DEFAULT 0",

        "exit_reason":
            "ALTER TABLE paper_positions ADD COLUMN exit_reason TEXT DEFAULT ''"
    }

    for column, sql in paper_migrations.items():

        if column not in existing_paper:

            try:
                conn.execute(sql)

            except Exception:
                pass

    conn.commit()
    conn.close()

# =========================================================
# HISTORIAL MARKET CAP
# =========================================================

# =========================================================
# HISTORIAL MARKET CAP
# =========================================================

def save_token_history(
    mint,
    market_cap,
    trader="",
    side="",
    signature="",
    source="live"
):

    if not mint:
        return

    market_cap = float(
        market_cap or 0
    )

    if market_cap <= 0:
        return

    conn = db()

    # Evitar guardar exactamente la misma
    # transacción dos veces.
    if signature:

        exists = conn.execute(
            """
            SELECT id
            FROM token_history
            WHERE signature = ?
            LIMIT 1
            """,
            (
                signature,
            )
        ).fetchone()

        if exists:

            conn.close()
            return

    conn.execute(
        """
        INSERT INTO token_history(
            ts,
            mint,
            market_cap_sol,
            trader,
            side,
            signature,
            source
        )

        VALUES(
            ?,?,?,?,?,?,?
        )
        """,
        (
            time.time(),
            mint,
            market_cap,
            trader,
            side,
            signature,
            source
        )
    )

    conn.commit()
    conn.close()


# =========================================================
# EVENTOS DE POSICIÓN
# =========================================================
def save_position_event(
    mint,
    event_type,
    market_cap,
    remaining_pct=0.0,
    realized_pnl_usd=0.0,
    details=""
):

    if not mint:
        return

    conn = db()

    conn.execute(
        """
        INSERT INTO position_events(
            ts,
            mint,
            event_type,
            market_cap_sol,
            remaining_pct,
            realized_pnl_usd,
            details
        )

        VALUES(
            ?,?,?,?,?,?,?
        )
        """,
        (
            time.time(),
            mint,
            str(event_type or ""),
            float(market_cap or 0),
            float(remaining_pct or 0),
            float(realized_pnl_usd or 0),
            str(details or "")
        )
    )

    conn.commit()
    conn.close()

# =========================================================
# IDENTIFICAR TRADER
# =========================================================

def trader_for(wallet):

    for name, watched_wallet in WATCHED.items():

        if wallet == watched_wallet:

            return name


    if wallet:

        return wallet[:6]


    return "unknown"


# =========================================================
# SCORE: TRADER
# =========================================================

def score_trader(trader):

    return TRADER_QUALITY.get(
        trader,
        15
    )


# =========================================================
# SCORE: MARKET CAP
# =========================================================

def score_token_structure(
    market_cap_sol
):

    mc = float(
        market_cap_sol or 0
    )


    # Muy pequeño = extremadamente especulativo
    if mc < 20:

        return 4


    # Zona temprana
    if mc < 50:

        return 12


    # Todavía relativamente temprano
    if mc < 100:

        return 15


    # Ya más avanzado
    if mc < 250:

        return 10


    return 5


# =========================================================
# SCORE: TAMAÑO DE LA COMPRA
# =========================================================

def score_trade_size(
    sol_amount
):

    amount = float(
        sol_amount or 0
    )


    if amount >= 5:

        return 15


    if amount >= 2:

        return 13


    if amount >= 1:

        return 10


    if amount >= 0.3:

        return 7


    return 3


# =========================================================
# SCORE: ENTRADA TEMPRANA
# =========================================================

def score_timing(
    mint,
    market_cap_sol
):

    conn = db()

    first = conn.execute(
        """
        SELECT market_cap_sol

        FROM trades

        WHERE mint = ?
        AND (
        side LIKE '%buy%'
        OR side = 'create'
        )

        ORDER BY ts ASC

        LIMIT 1
        """,
        (
            mint,
        )
    ).fetchone()

    conn.close()


    if not first:

        return 20


    first_mc = float(
        first[0] or 0
    )

    current_mc = float(
        market_cap_sol or 0
    )


    if first_mc <= 0:

        return 15


    change = (
        current_mc
        -
        first_mc
    ) / first_mc


    # Seguimos prácticamente junto al primer trader
    if change <= 0.05:

        return 20


    if change <= 0.15:

        return 17


    if change <= 0.30:

        return 12


    if change <= 0.60:

        return 6


    # Ya explotó demasiado desde la primera señal
    return 1


# =========================================================
# SCORE: CONSENSO
# =========================================================

def score_consensus(
    mint,
    current_trader
):

    cutoff = (
        time.time()
        -
        WINDOW
    )


    conn = db()

    rows = conn.execute(
        """
        SELECT DISTINCT trader

        FROM trades

        WHERE mint = ?
        AND ts > ?
        AND (
        side LIKE '%buy%'
        OR side = 'create'
        )
        """,
        (
            mint,
            cutoff
        )
    ).fetchall()

    conn.close()


    traders = {
        row[0]
        for row in rows
    }


    # Compra de un solo trader:
    # no penalizamos.
    if len(traders) <= 1:

        return 0


    # 2 traders
    if len(traders) == 2:

        return 5


    # 3+
    return 10


# =========================================================
# SCORE: CONTEXTO GENERAL
# =========================================================
def score_market_context():

    conn = db()

    since = time.time() - 120

    rows = conn.execute(
        """
        SELECT
            trader,
            side,
            sol

        FROM trades

        WHERE ts >= ?
        AND source = 'live'
        """,
        (
            since,
        )
    ).fetchall()

    conn.close()


    # No hay suficiente información reciente.
    if len(rows) < 3:
        return 5


    # Evitamos que un trader que haga muchas
    # ventas pequeñas domine todo el cálculo.
    trader_flow = {}

    for trader, side, sol in rows:

        trader = str(
            trader or "unknown"
        )

        side = str(
            side or ""
        ).lower()

        sol = abs(
            float(
                sol or 0
            )
        )

        if trader not in trader_flow:
            trader_flow[trader] = 0.0


        if (
            "buy" in side
            or side == "create"
        ):

            trader_flow[trader] += sol


        elif "sell" in side:

            trader_flow[trader] -= sol


    bullish = 0
    bearish = 0
    neutral = 0

    for flow in trader_flow.values():

        if flow > 0.05:
            bullish += 1

        elif flow < -0.05:
            bearish += 1

        else:
            neutral += 1


    total = (
        bullish
        + bearish
        + neutral
    )

    if total == 0:
        return 5


    bullish_ratio = (
        bullish / total
    )


    # Muy fuerte comprador
    if bullish_ratio >= 0.80:
        return 10

    # Fuerte comprador
    if bullish_ratio >= 0.65:
        return 8

    # Ligeramente comprador
    if bullish_ratio >= 0.55:
        return 7

    # Neutral
    if bullish_ratio >= 0.45:
        return 5

    # Ligeramente vendedor
    if bullish_ratio >= 0.30:
        return 3

    # Muy vendedor
    return 1


# =========================================================
# DECISIÓN DEL AGENTE
# =========================================================

def decision_from_score(score):

    if score >= 80:

        return "COPY"


    if score >= 60:

        return "WATCH"


    return "SKIP"


# =========================================================
# ANALIZAR COMPRA
# =========================================================

def evaluate_buy(
    trader,
    event,
    source="live"
):

    mint = (
        event.get("mint")
        or ""
    )


    sol_amount = float(
        event.get("solAmount")
        or event.get("amount")
        or 0
    )


    market_cap = float(
        event.get("marketCapSol")
        or event.get("market_cap_sol")
        or 0
    )


    signature = (
        event.get("signature")
        or "eval-" + uuid.uuid4().hex
    )


    trader_score = score_trader(
        trader
    )


    timing_score = score_timing(
        mint,
        market_cap
    )


    size_score = score_trade_size(
        sol_amount
    )


    token_score = score_token_structure(
        market_cap
    )


    consensus_score = score_consensus(
        mint,
        trader
    )


    market_score = score_market_context()


    score = (
        trader_score
        +
        timing_score
        +
        size_score
        +
        token_score
        +
        consensus_score
        +
        market_score
    )


    score = min(
        100,
        int(score)
    )


    decision = decision_from_score(
        score
    )


    reasons = []


    reasons.append(
        f"Trader @{trader}: "
        f"{trader_score}/30"
    )


    reasons.append(
        f"Entrada: "
        f"{timing_score}/20"
    )


    reasons.append(
        f"Tamaño operación: "
        f"{size_score}/15"
    )


    reasons.append(
        f"Estructura/token: "
        f"{token_score}/15"
    )


    if consensus_score > 0:

        reasons.append(
            f"Consenso adicional: "
            f"+{consensus_score}"
        )

    else:

        reasons.append(
            "Sin consenso adicional"
        )


    reasons.append(
        f"Mercado general: "
        f"{market_score}/10"
    )


    conn = db()


    try:

        conn.execute(
            """
            INSERT INTO evaluations(

                trade_signature,

                ts,

                trader,

                mint,

                source,

                score,

                decision,

                trader_score,

                timing_score,

                size_score,

                token_score,

                consensus_score,

                market_score,

                reasons

            )

            VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                signature,

                time.time(),

                trader,

                mint,

                source,

                score,

                decision,

                trader_score,

                timing_score,

                size_score,

                token_score,

                consensus_score,

                market_score,

                json.dumps(reasons)
            )
        )

        conn.commit()

    except sqlite3.IntegrityError:

        pass


    conn.close()


    print(
        f"[AGENT] {decision} | "
        f"{score}/100 | "
        f"@{trader} | "
        f"{mint}"
    )


    # PAPER TRADING
    #
    # Solo abrimos posición paper
    # cuando el agente dice COPY.
    #
    # Seguimos SIN ejecutar dinero real.

    if decision == "COPY":

        open_paper_position(
            mint=mint,
            trader=trader,
            market_cap=market_cap,
            score=score,
            decision=decision
        )


    return {
        "score": score,
        "decision": decision,
        "reasons": reasons
    }


# =========================================================
# PAPER POSITION
# =========================================================

def open_paper_position(
    mint,
    trader,
    market_cap,
    score,
    decision,
    mode="paper"
):

    if market_cap <= 0:
        return

    risk = risk_check(
    mint=mint,
    amount_usd=PAPER_BUY_USD,
    mode=mode
)
    if not risk["ok"]:
        print(
            f"[RISK BLOCK] No se abre {mint}: "
            f"{risk['reason']}"
        )
        return

    conn = db()

    daily_pnl = get_daily_realized_pnl(
    mode=mode
)

    if daily_pnl <= -MAX_DAILY_LOSS_USD:
        print(
            f"[RISK BLOCK] No se abre {mint}: "
            f"pérdida diaria ${daily_pnl:.2f} "
            f"alcanzó el límite "
            f"-${MAX_DAILY_LOSS_USD:.2f}"
        )
        return

    if count_open_positions(mode=mode) >= 1:
        print(
            f"[RISK BLOCK] No se abre {mint}: "
            f"ya existe una posición abierta"
        )
        return

    exists = conn.execute(
        """
        SELECT id
        FROM paper_positions
        WHERE mint = ?
        AND status = 'open'
        """,
        (mint,)
    ).fetchone()

    if exists:

        conn.close()
        return

    conn.execute(
    """
    INSERT INTO paper_positions(
        opened_ts,
        mint,
        trigger_traders,
        entry_mc,
        stake_usd,
        status,
        pnl_usd,
        decision,
        score,
        origin_trader,
        current_mc,
        remaining_pct,
        realized_pnl_usd,
        unrealized_pnl_usd,
        last_action,
        tp_stage,
        mode
    )
    VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
    )
    """,
    (
        time.time(),
        mint,
        json.dumps([trader]),
        market_cap,
        PAPER_BUY_USD,
        "open",
        0,
        decision,
        score,
        trader,
        market_cap,
        1.0,
        0,
        0,
        "HOLD",
        0,
        mode
    )
)

    conn.commit()
    conn.close()
    


    if not mint.startswith("DEMO"):
        TRACKED_TOKENS.add(mint)

    print(
        f"[PAPER OPEN] "
        f"${PAPER_BUY_USD} | "
        f"@{trader} | "
        f"{mint} | "
        f"MC {market_cap:.2f} | "
        f"{score}/100"
    )

    save_position_event(
        mint=mint,
        event_type="OPEN",
        market_cap=market_cap,
        remaining_pct=100,
        realized_pnl_usd=0,
        details=f"Opened by {trader}"
    )



def update_paper_position(
    mint,
    trader,
    side,
    market_cap,
    new_token_balance
):

    if not mint or market_cap <= 0:
        return

    conn = db()

    position = conn.execute(
        """
        SELECT
            id,
            entry_mc,
            stake_usd,
            remaining_pct,
            realized_pnl_usd,
            origin_trader,
            tp_stage

        FROM paper_positions

        WHERE mint = ?
        AND status = 'open'

        ORDER BY id DESC
        LIMIT 1
        """,
        (mint,)
    ).fetchone()

    if not position:

        conn.close()
        return


    position_id = position[0]
    entry_mc = float(position[1] or 0)
    stake_usd = float(position[2] or 0)
    remaining = float(position[3] or 0)
    realized = float(position[4] or 0)
    origin_trader = position[5] or ""
    tp_stage = int(position[6] or 0)


    if entry_mc <= 0 or remaining <= 0:

        conn.close()
        return


    change_pct = (
        market_cap / entry_mc
        -
        1
    )


    unrealized = (
        stake_usd
        *
        remaining
        *
        change_pct
    )


    action = "HOLD"
    exit_reason = ""
    sell_fraction = 0.0


    # =========================================
    # 1. STOP LOSS
    # =========================================

    if change_pct <= -0.20:

        action = "STOP LOSS"
        sell_fraction = remaining
        exit_reason = "Caída del 20% desde la entrada"


    # =========================================
    # 2. TRADER ORIGINAL VENDE TODO
    # =========================================

    elif (
        "sell" in side
        and trader == origin_trader
        and float(new_token_balance or 0) <= 0
    ):

        action = "EXIT"
        sell_fraction = remaining
        exit_reason = (
            f"@{origin_trader} cerró su posición"
        )

        # =========================================
    # 3. TAKE PROFIT
    # =========================================
    elif change_pct >= 0.25 and tp_stage < 3:

        if change_pct >= 1.00:
            target_stage = 3
        elif change_pct >= 0.50:
            target_stage = 2
        else:
            target_stage = 1

        missing_stages = target_stage - tp_stage

        # Cada nivel vende 25% de la posición original.
        # Si el precio salta varios niveles,
        # ejecutamos todos los tramos pendientes.
        sell_fraction = 0.25 * missing_stages

        # Nunca vender más de lo que queda abierto.
        sell_fraction = min(
            sell_fraction,
            remaining
        )

        tp_stage = target_stage

        if target_stage == 3:
            action = "TAKE PROFIT +100%"
        elif target_stage == 2:
            action = "TAKE PROFIT +50%"
        else:
            action = "TAKE PROFIT +25%"


    # =========================================
    # 6. TRADER ORIGINAL VENDE PARCIALMENTE
    # =========================================

    elif (
        "sell" in side
        and trader == origin_trader
    ):

        action = "PARTIAL SELL"

        sell_fraction = min(
            0.25,
            remaining
        )


    # =========================================
    # EJECUTAR VENTA PAPER
    # =========================================

    if sell_fraction > 0:

        realized_from_sell = (
            stake_usd
            *
            sell_fraction
            *
            change_pct
        )

        realized += realized_from_sell

        remaining -= sell_fraction

        remaining = max(
            0,
            remaining
        )


    # Recalcular PnL no realizado después de vender

    unrealized = (
        stake_usd
        *
        remaining
        *
        change_pct
    )


    total_pnl = (
        realized
        +
        unrealized
    )


    status = "open"
    closed_ts = 0
    exit_mc = 0


    if remaining <= 0.000001:

        status = "closed"
        remaining = 0
        unrealized = 0
        total_pnl = realized
        closed_ts = time.time()
        exit_mc = market_cap

        if not exit_reason:
            exit_reason = action

        TOKENS_TO_UNSUBSCRIBE.add(mint)

        TRACKED_TOKENS.discard(mint)
        SUBSCRIBED_TOKENS.discard(mint)


    conn.execute(
        """
        UPDATE paper_positions

        SET
            current_mc = ?,
            remaining_pct = ?,
            realized_pnl_usd = ?,
            unrealized_pnl_usd = ?,
            pnl_usd = ?,
            last_action = ?,
            tp_stage = ?,
            status = ?,
            closed_ts = ?,
            exit_mc = ?,
            exit_reason = ?

        WHERE id = ?
        """,
        (
            market_cap,
            remaining,
            realized,
            unrealized,
            total_pnl,
            action,
            tp_stage,
            status,
            closed_ts,
            exit_mc,
            exit_reason,
            position_id
        )
    )

    conn.commit()
    conn.close()

    if action != "HOLD":

        save_position_event(
        mint=mint,
        event_type=action,
        market_cap=market_cap,
        remaining_pct=remaining * 100,
        realized_pnl_usd=realized,
        details=exit_reason
        )


    print(
        f"[POSITION] {action} | "
        f"{mint} | "
        f"{change_pct * 100:+.1f}% | "
        f"restante {remaining * 100:.0f}% | "
        f"PnL ${total_pnl:+.2f}"
    )

# =========================================================
# GUARDAR TRADE
# =========================================================

def save_trade(
    trader,
    wallet,
    event,
    source="live"
):

    side = str(
        event.get("txType")
        or event.get("type")
        or ""
    ).lower()


    mint = (
        event.get("mint")
        or ""
    )


    sol = float(
        event.get("solAmount")
        or event.get("amount")
        or 0
    )


    market_cap = float(
        event.get("marketCapSol")
        or event.get("market_cap_sol")
        or 0
    )


    signature = (
        event.get("signature")
        or ""
    )


    token_amount = float(
        event.get("tokenAmount")
        or 0
    )


    new_token_balance = float(
        event.get("newTokenBalance")
        or 0
    )


    pool = (
        event.get("pool")
        or ""
    )


    conn = db()


    conn.execute(
        """
        INSERT INTO trades(

            ts,

            trader,

            wallet,

            side,

            mint,

            sol,

            market_cap_sol,

            signature,

            source,

            token_amount,

            new_token_balance,

            pool

        )

        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            time.time(),

            trader,

            wallet,

            side,

            mint,

            sol,

            market_cap,

            signature,

            source,

            token_amount,

            new_token_balance,

            pool
        )
    )


    conn.commit()
    conn.close()

    save_token_history(
        mint=mint,
        market_cap=market_cap,
        trader=trader,
        side=side,
        signature=signature,
        source=source
    )

    # Cada BUY ahora se analiza individualmente.

    is_buy = "buy" in side

    is_create_with_buy = (
        side == "create"
        and float(event.get("initialBuy") or 0) > 0
        and float(event.get("solAmount") or 0) > 0
    )

    if is_buy or is_create_with_buy:
        evaluate_buy(
            trader,
            event,
            source
        )

    # Actualizar cualquier posición paper
    # abierta en este token.
    update_paper_position(
        mint=mint,
        trader=trader,
        side=side,
        market_cap=market_cap,
        new_token_balance=new_token_balance
    )
# =========================================================
# STREAM REAL PUMPPORTAL
# =========================================================

async def stream():

    global FORCE_STREAM_ERROR

    if not API_KEY:

        print(
            "[STREAM] Falta PUMPPORTAL_API_KEY"
        )

        return


    uri = (
        "wss://pumpportal.fun/api/data"
        f"?api-key={API_KEY}"
    )


    while True:

        try:

            async with websockets.connect(
                uri,
                ping_interval=20
            ) as websocket:

                # Cada reconexión empieza
                # con suscripciones limpias.
                SUBSCRIBED_TOKENS.clear()


                # =========================================
                # SUSCRIBIR TRADERS VIGILADOS
                # =========================================

                account_payload = {
                    "method": "subscribeAccountTrade",
                    "keys": list(WATCHED.values())
                }

                await websocket.send(
                    json.dumps(account_payload)
                )


                # =========================================
                # SUSCRIBIR TOKENS PAPER YA ABIERTOS
                # =========================================

                if TRACKED_TOKENS:

                    token_payload = {
                        "method": "subscribeTokenTrade",
                        "keys": list(TRACKED_TOKENS)
                    }

                    await websocket.send(
                        json.dumps(token_payload)
                    )

                    SUBSCRIBED_TOKENS.update(
                        TRACKED_TOKENS
                    )

                    print(
                        f"[TRACKER] Suscrito a "
                        f"{len(SUBSCRIBED_TOKENS)} token(s)"
                    )


                print(
                    "[STREAM] Conectado a PumpPortal"
                )


                # =========================================
                # LOOP PRINCIPAL
                # =========================================

                while True:

                    # Ver si apareció una nueva posición
                    # COPY mientras el programa está abierto.

                    pending_tokens = (
                        TRACKED_TOKENS
                        -
                        SUBSCRIBED_TOKENS
                    )

                    


                    if pending_tokens:

                        token_payload = {
                            "method": "subscribeTokenTrade",
                            "keys": list(pending_tokens)
                        }

                        await websocket.send(
                            json.dumps(token_payload)
                        )

                        SUBSCRIBED_TOKENS.update(
                            pending_tokens
                        )

                        print(
                            f"[TRACKER] Nueva suscripción: "
                            f"{len(pending_tokens)} token(s)"
                        )

                    if TOKENS_TO_UNSUBSCRIBE:

                        unsubscribe_payload = {
                            "method": "unsubscribeTokenTrade",
                            "keys": list(TOKENS_TO_UNSUBSCRIBE)
                        }

                        await websocket.send(
                            json.dumps(unsubscribe_payload)
                        )

                        print(
                            f"[TRACKER] Desuscrito de "
                            f"{len(TOKENS_TO_UNSUBSCRIBE)} token(s)"
                        )

                        TOKENS_TO_UNSUBSCRIBE.clear()

                        

                    if FORCE_STREAM_ERROR:
                        FORCE_STREAM_ERROR = False
                        raise RuntimeError(
                            "DEMO FORCED STREAM ERROR"
                        )


                    # Esperamos como máximo 1 segundo.
                    # Así podemos seguir comprobando
                    # nuevas posiciones aunque no lleguen trades.

                    try:

                        raw = await asyncio.wait_for(
                            websocket.recv(),
                            timeout=1.0
                        )

                    except asyncio.TimeoutError:

                        continue


                    event = json.loads(raw)

                    if "message" in event:
                        print(
                            "[PUMPPORTAL]",
                            event["message"]
                        )
                        continue

                                        # =========================================================
                    # PROTECCIÓN CONTRA EVENTOS DUPLICADOS
                    # =========================================================
                    signature = event.get("signature")

                    if signature:

                        if signature in SEEN_SIGNATURES:
                            print(
                                f"[DUPLICATE MEMORY] Ignorado {signature[:8]}..."
                            )
                            continue

                        if not mark_signature_processed(
                            signature,
                            source="live"
                        ):
                            print(
                                f"[DUPLICATE DB] Ignorado {signature[:8]}..."
                            )
                            continue

                        SEEN_SIGNATURES.add(signature)

                        if len(SEEN_SIGNATURES) > 5000:
                            SEEN_SIGNATURES.clear()


                    wallet = (
                        event.get("traderPublicKey")
                        or event.get("user")
                        or event.get("wallet")
                        or ""
                    )

                    mint = (
                        event.get("mint")
                        or ""
                    )

                    


                    wallet = (
                        event.get("traderPublicKey")
                        or event.get("user")
                        or event.get("wallet")
                        or ""
                    )


                    mint = (
                        event.get("mint")
                        or ""
                    )


                    is_watched_wallet = (
                        wallet in WATCHED.values()
                    )


                    is_tracked_token = (
                        mint in TRACKED_TOKENS
                    )


                    # =====================================
                    # UNO DE NUESTROS 5 TRADERS OPERÓ
                    # =====================================

                    if is_watched_wallet:

                        trader = trader_for(
                            wallet
                        )

                        print(
                            f"[TRADE LIVE] "
                            f"{trader}: "
                            f"{event}"
                        )

                        save_trade(
                            trader,
                            wallet,
                            event,
                            source="live"
                        )


                    # =====================================
                    # CUALQUIER OTRA WALLET OPERÓ
                    # UN TOKEN QUE TENEMOS EN PAPER
                    # =====================================

                    if is_tracked_token:

                        print(
                            f"[TOKEN LIVE] "
                            f"{mint}: "
                            f"{event}"
                        )

                        save_token_history(
                            mint=mint,

                            market_cap=float(
                                event.get("marketCapSol")
                                or event.get(
                                    "market_cap_sol"
                                )
                                or 0
                            ),

                            trader=trader_for(
                                wallet
                            ),

                            side=str(
                                event.get("txType")
                                or event.get("type")
                                or ""
                            ).lower(),

                            signature=event.get(
                                "signature"
                            ) or "",

                            source="token-live"
                        )

                        update_paper_position(
                            mint=mint,

                            trader=trader_for(
                                wallet
                            ),

                            side=str(
                                event.get("txType")
                                or event.get("type")
                                or ""
                            ).lower(),

                            market_cap=float(
                                event.get("marketCapSol")
                                or event.get(
                                    "market_cap_sol"
                                )
                                or 0
                            ),

                            new_token_balance=float(
                                event.get(
                                    "newTokenBalance"
                                )
                                or 0
                            )
                        )


        except Exception as ex:

            print(
                "[STREAM ERROR]",
                repr(ex)
            )

            await asyncio.sleep(3)


# =========================================================
# STARTUP
# =========================================================

@app.on_event("startup")
async def startup():

    global KILL_SWITCH

    KILL_SWITCH = get_persistent_kill_switch()

    print(
        f"[STARTUP] KILL_SWITCH = {KILL_SWITCH}"
    )

    migrate_database()

    # Recuperar tokens de posiciones paper abiertas

    conn = db()

    rows = conn.execute(
        """
        SELECT mint
        FROM paper_positions
        WHERE status = 'open'
        """
    ).fetchall()

    conn.close()

    for row in rows:
        mint = row[0]

        if mint and not mint.startswith("DEMO"):
            TRACKED_TOKENS.add(mint)
    print(
        f"[TRACKER] {len(TRACKED_TOKENS)} "
        f"tokens abiertos recuperados"
    )

    asyncio.create_task(
        stream()
    )

# =========================================================
# AUTENTICACIÓN
# =========================================================

def auth(x_app_token):

    if x_app_token != APP_TOKEN:

        raise HTTPException(
            401,
            "Invalid token"
        )

def require_debug_mode():

    if not DEBUG_MODE:
        raise HTTPException(
            status_code=404,
            detail="NOT_FOUND"
        )

def require_live_trading():

    if not LIVE_TRADING:
        raise HTTPException(
            status_code=403,
            detail="LIVE_TRADING_DISABLED"
        )
    
def get_persistent_kill_switch():

    conn = db()

    row = conn.execute(
        """
        SELECT value
        FROM app_state
        WHERE key = ?
        LIMIT 1
        """,
        (
            "KILL_SWITCH",
        )
    ).fetchone()

    conn.close()

    if not row:
        return False

    return str(row[0]).lower() == "true"


def set_persistent_kill_switch(enabled):

    conn = db()

    conn.execute(
        """
        INSERT INTO app_state(
            key,
            value
        )
        VALUES(?,?)
        ON CONFLICT(key)
        DO UPDATE SET value = excluded.value
        """,
        (
            "KILL_SWITCH",
            "true" if enabled else "false"
        )
    )

    conn.commit()
    conn.close()

    return enabled

# =========================================================
# STATUS
# =========================================================

@app.get("/api/status")
def status(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)


    return {

        "ok":
            True,

        "version":
            "V3",

        "watched":
            WATCHED,

        "live_stream_configured":
            bool(API_KEY),

        "paper_buy_usd":
            PAPER_BUY_USD,

        "consensus_window_seconds":
            WINDOW
    }


# =========================================================
# TRADES
# =========================================================

@app.get("/api/trades")
def trades(
    x_app_token: str = Header(default=""),
    limit: int = 100
):

    auth(x_app_token)


    conn = db()


    rows = conn.execute(
        """
        SELECT

            ts,

            trader,

            side,

            mint,

            sol,

            market_cap_sol,

            signature,

            source,

            token_amount,

            new_token_balance,

            pool

        FROM trades

        ORDER BY id DESC

        LIMIT ?
        """,
        (
            min(
                limit,
                300
            ),
        )
    ).fetchall()


    conn.close()


    return [

        {

            "ts":
                r[0],

            "trader":
                r[1],

            "side":
                r[2],

            "mint":
                r[3],

            "sol":
                r[4],

            "market_cap_sol":
                r[5],

            "signature":
                r[6],

            "source":
                r[7],

            "token_amount":
                r[8],

            "new_token_balance":
                r[9],

            "pool":
                r[10]

        }

        for r in rows
    ]


# =========================================================
# DECISIONES DEL AGENTE
# =========================================================

@app.get("/api/evaluations")
def evaluations(
    x_app_token: str = Header(default=""),
    limit: int = 100
):

    auth(x_app_token)


    conn = db()


    rows = conn.execute(
        """
        SELECT

            ts,

            trader,

            mint,

            source,

            score,

            decision,

            trader_score,

            timing_score,

            size_score,

            token_score,

            consensus_score,

            market_score,

            reasons

        FROM evaluations

        ORDER BY id DESC

        LIMIT ?
        """,
        (
            min(
                limit,
                200
            ),
        )
    ).fetchall()


    conn.close()


    return [

        {

            "ts":
                r[0],

            "trader":
                r[1],

            "mint":
                r[2],

            "source":
                r[3],

            "score":
                r[4],

            "decision":
                r[5],

            "trader_score":
                r[6],

            "timing_score":
                r[7],

            "size_score":
                r[8],

            "token_score":
                r[9],

            "consensus_score":
                r[10],

            "market_score":
                r[11],

            "reasons":
                json.loads(
                    r[12]
                )

        }

        for r in rows
    ]


# =========================================================
# SEÑALES
# =========================================================

@app.get("/api/signals")
def signals(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)


    conn = db()


    rows = conn.execute(
        """
        SELECT

            ts,

            trader,

            mint,

            source,

            score,

            decision,

            reasons

        FROM evaluations

        ORDER BY id DESC

        LIMIT 50
        """
    ).fetchall()


    conn.close()


    return [

        {

            "ts":
                r[0],

            "trader":
                r[1],

            "mint":
                r[2],

            "source":
                r[3],

            "score":
                r[4],

            "decision":
                r[5],

            "reasons":
                json.loads(
                    r[6]
                )

        }

        for r in rows
    ]


# =========================================================
# PAPER
# =========================================================

@app.get("/api/paper")
def paper(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)

    conn = db()

    rows = conn.execute(
        """
        SELECT
            opened_ts,
            mint,
            trigger_traders,
            entry_mc,
            stake_usd,
            status,
            pnl_usd,
            decision,
            score,
            origin_trader,
            current_mc,
            remaining_pct,
            realized_pnl_usd,
            unrealized_pnl_usd,
            last_action,
            tp_stage,
            closed_ts,
            exit_mc,
            exit_reason

        FROM paper_positions

        ORDER BY id DESC

        LIMIT 100
        """
    ).fetchall()

    conn.close()

    return [
        {
            "opened_ts": r[0],
            "mint": r[1],
            "traders": json.loads(r[2]),
            "entry_mc": r[3],
            "stake_usd": r[4],
            "status": r[5],
            "pnl_usd": r[6],
            "decision": r[7],
            "score": r[8],
            "origin_trader": r[9],
            "current_mc": r[10],
            "remaining_pct": r[11],
            "realized_pnl_usd": r[12],
            "unrealized_pnl_usd": r[13],
            "last_action": r[14],
            "tp_stage": r[15],
            "closed_ts": r[16],
            "exit_mc": r[17],
            "exit_reason": r[18]
        }

        for r in rows
    ]

# =========================================================
# HISTORIAL DE TOKEN
# =========================================================

@app.get("/api/token-history/{mint}")
def token_history(
    mint: str,
    x_app_token: str = Header(default=""),
    limit: int = 500
):

    auth(x_app_token)

    limit = max(
        1,
        min(
            int(limit),
            2000
        )
    )

    conn = db()

    rows = conn.execute(
        """
        SELECT
            ts,
            market_cap_sol,
            trader,
            side,
            signature,
            source

        FROM token_history

        WHERE mint = ?

        ORDER BY ts DESC

        LIMIT ?
        """,
        (
            mint,
            limit
        )
    ).fetchall()

    conn.close()

    rows.reverse()

    return [
        {
            "ts": r[0],
            "market_cap_sol": r[1],
            "trader": r[2],
            "side": r[3],
            "signature": r[4],
            "source": r[5]
        }

        for r in rows
    ]

# =========================================================
# EVENTOS DE POSICIÓN
# =========================================================

@app.get("/api/position-events/{mint}")
def position_events(
    mint: str,
    x_app_token: str = Header(default=""),
    limit: int = 100
):

    auth(x_app_token)

    limit = max(
        1,
        min(
            int(limit),
            500
        )
    )

    conn = db()

    rows = conn.execute(
        """
        SELECT
            ts,
            event_type,
            market_cap_sol,
            remaining_pct,
            realized_pnl_usd,
            details

        FROM position_events

        WHERE mint = ?

        ORDER BY ts ASC

        LIMIT ?
        """,
        (
            mint,
            limit
        )
    ).fetchall()

    conn.close()

    return [
        {
            "ts": r[0],
            "event_type": r[1],
            "market_cap_sol": r[2],
            "remaining_pct": r[3],
            "realized_pnl_usd": r[4],
            "details": r[5]
        }

        for r in rows
    ]
# =========================================================
# ESTADÍSTICAS TRADERS
# =========================================================

@app.get("/api/trader-stats")
def trader_stats(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)


    conn = db()

    result = []


    for name, wallet in WATCHED.items():

        buys = conn.execute(
            """
            SELECT COUNT(*)

            FROM trades

            WHERE trader = ?
            AND (
            side LIKE '%buy%'
            OR side = 'create'
            )
            """,
            (
                name,
            )
        ).fetchone()[0]


        sells = conn.execute(
            """
            SELECT COUNT(*)

            FROM trades

            WHERE trader = ?
            AND side LIKE '%sell%'
            """,
            (
                name,
            )
        ).fetchone()[0]


        evaluations_count = conn.execute(
            """
            SELECT COUNT(*)

            FROM evaluations

            WHERE trader = ?
            """,
            (
                name,
            )
        ).fetchone()[0]


        avg_score = conn.execute(
            """
            SELECT AVG(score)

            FROM evaluations

            WHERE trader = ?
            """,
            (
                name,
            )
        ).fetchone()[0]


        result.append(
            {

                "name":
                    name,

                "wallet":
                    wallet,

                "buys":
                    buys,

                "sells":
                    sells,

                "events":
                    buys + sells,

                "evaluations":
                    evaluations_count,

                "avg_score":
                    round(
                        avg_score or 0,
                        1
                    ),

                "quality":
                    TRADER_QUALITY.get(
                        name,
                        15
                    )

            }
        )


    conn.close()

    return result


# =========================================================
# DEMO V3
# =========================================================

@app.post("/api/demo")
def demo(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()


    names = list(
        WATCHED.keys()
    )


    trader = random.choice(
        names
    )


    mint = (
        "DEMO"
        +
        uuid.uuid4().hex[:12].upper()
    )


    event = {

        "txType":
            "buy",

        "mint":
            mint,

        "solAmount":
            round(
                random.uniform(
                    0.2,
                    5
                ),
                3
            ),

        "marketCapSol":
            round(
                random.uniform(
                    15,
                    200
                ),
                2
            ),

        "tokenAmount":
            random.uniform(
                100000,
                5000000
            ),

        "newTokenBalance":
            random.uniform(
                100000,
                5000000
            ),

        "pool":
            "pump",

        "signature":
            "demo-"
            +
            uuid.uuid4().hex

    }


    save_trade(
        trader,
        WATCHED[trader],
        event,
        source="demo"
    )


    return {

        "ok":
            True,

        "mint":
            mint,

        "trader":
            trader
    }

@app.post("/api/demo-tp2")
def demo_tp2(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "9m2V3MtBw6nbcXVWLVose3HcpN6wKryr4Egx8Dsypump"

    entry_mc = 45.85
    tp2_mc = entry_mc * 1.60

    update_paper_position(
        mint=mint,
        trader="test-market",
        side="buy",
        market_cap=tp2_mc,
        new_token_balance=1
    )

    return {
        "ok": True,
        "mint": mint,
        "market_cap": tp2_mc,
        "expected": "TAKE PROFIT +50%"
    }

@app.post("/api/demo-tp3")
def demo_tp3(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "9m2V3MtBw6nbcXVWLVose3HcpN6wKryr4Egx8Dsypump"

    entry_mc = 45.85
    tp3_mc = entry_mc * 2.10

    update_paper_position(
        mint=mint,
        trader="test-market",
        side="buy",
        market_cap=tp3_mc,
        new_token_balance=1
    )

    return {
        "ok": True,
        "mint": mint,
        "market_cap": tp3_mc,
        "expected": "TAKE PROFIT +100%"
    }

@app.post("/api/demo-stop")
def demo_stop(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "9m2V3MtBw6nbcXVWLVose3HcpN6wKryr4Egx8Dsypump"

    entry_mc = 45.85
    stop_mc = entry_mc * 0.75

    update_paper_position(
        mint=mint,
        trader="test-market",
        side="sell",
        market_cap=stop_mc,
        new_token_balance=1
    )

    return {
        "ok": True,
        "mint": mint,
        "market_cap": stop_mc,
        "expected": "STOP LOSS"
    }

@app.post("/api/demo-exit-open")
def demo_exit_open(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "DEMO-EXIT-001"
    trader = "test-exit"
    market_cap = 50.0

    open_paper_position(
        mint=mint,
        trader=trader,
        market_cap=market_cap,
        score=99,
        decision="COPY"
    )

    return {
        "ok": True,
        "mint": mint,
        "entry_mc": market_cap,
        "trader": trader
    }

@app.post("/api/demo-exit-close")
def demo_exit_close(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "DEMO-EXIT-001"

    update_paper_position(
        mint=mint,
        trader="test-exit",
        side="sell",
        market_cap=52.0,
        new_token_balance=0
    )

    return {
        "ok": True,
        "mint": mint,
        "market_cap": 52.0,
        "expected": "EXIT"
    }

@app.post("/api/demo-partial-open")
def demo_partial_open(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "DEMO-PARTIAL-001"
    trader = "test-partial"
    market_cap = 50.0

    open_paper_position(
        mint=mint,
        trader=trader,
        market_cap=market_cap,
        score=99,
        decision="COPY"
    )

    return {
        "ok": True,
        "mint": mint,
        "entry_mc": market_cap,
        "trader": trader
    }

@app.post("/api/demo-partial-sell")
def demo_partial_sell(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "DEMO-PARTIAL-001"

    update_paper_position(
        mint=mint,
        trader="test-partial",
        side="sell",
        market_cap=52.0,
        new_token_balance=500
    )

    return {
        "ok": True,
        "mint": mint,
        "market_cap": 52.0,
        "expected": "PARTIAL SELL"
    }

@app.post("/api/demo-profit")
def demo_profit(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    row = conn.execute(
        """
        SELECT
            mint,
            origin_trader,
            entry_mc

        FROM paper_positions

        WHERE status = 'open'
        AND mint LIKE 'DEMO%'

        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            404,
            "No open demo position"
        )

    mint = row[0]
    trader = row[1]
    entry_mc = float(row[2] or 0)

    new_mc = entry_mc * 1.30

    update_paper_position(
        mint=mint,
        trader=trader,
        side="price_update",
        market_cap=new_mc,
        new_token_balance=1
    )

    return {
        "ok": True,
        "mint": mint,
        "old_mc": entry_mc,
        "new_mc": new_mc,
        "change_pct": 30
    }

@app.post("/api/demo-live-position")
def demo_live_position(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "9m2V3MtBw6nbcXVWLVose3HcpN6wKryr4Egx8Dsypump"
    trader = "test-live"
    market_cap = 45.85

    open_paper_position(
        mint=mint,
        trader=trader,
        market_cap=market_cap,
        score=99,
        decision="COPY"
    )

    return {
        "ok": True,
        "mint": mint,
        "entry_mc": market_cap
    }


@app.post("/api/demo-duplicate-check")
def demo_duplicate_check(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    signature = "DEMO-DUPLICATE-001"

    first = mark_signature_processed(
        signature,
        source="demo"
    )

    second = mark_signature_processed(
        signature,
        source="demo"
    )

    return {
        "ok": True,
        "signature": signature,
        "first_attempt": first,
        "second_attempt": second
    }

@app.post("/api/demo-invalid-event")
def demo_invalid_event(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    try:

        update_paper_position(
            mint="",
            trader="",
            side="",
            market_cap=0,
            new_token_balance=0
        )

        return {
            "ok": True,
            "result": "INVALID EVENT IGNORED"
        }

    except Exception as e:

        return {
            "ok": False,
            "error": str(e)
        }
    
@app.post("/api/demo-stream-error")
def demo_stream_error(
    x_app_token: str = Header(default="")
):

    global FORCE_STREAM_ERROR

    auth(x_app_token)
    require_debug_mode()

    FORCE_STREAM_ERROR = True

    return {
        "ok": True,
        "result": "STREAM ERROR ARMED"
    }


@app.get("/api/demo-daily-pnl")
def demo_daily_pnl(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    daily_pnl = get_daily_realized_pnl()

    return {
        "ok": True,
        "daily_realized_pnl": daily_pnl,
        "max_daily_loss": MAX_DAILY_LOSS_USD
    }


@app.post("/api/kill-switch/{state}")
def set_kill_switch(
    state: str,
    x_app_token: str = Header(default="")
):

    auth(x_app_token)

    global KILL_SWITCH

    normalized = state.strip().lower()

    if normalized not in (
        "on",
        "off"
    ):
        raise HTTPException(
            status_code=400,
            detail="INVALID_KILL_SWITCH_STATE"
        )

    KILL_SWITCH = normalized == "on"

    set_persistent_kill_switch(
        KILL_SWITCH
    )

    return {
        "ok": True,
        "kill_switch": KILL_SWITCH
    }

@app.get("/api/demo-slippage-check")
def demo_slippage_check(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    safe = validate_slippage(
        expected_price=100.0,
        execution_price=103.0
    )

    blocked = validate_slippage(
        expected_price=100.0,
        execution_price=107.0
    )

    return {
        "ok": True,
        "safe_3_percent": safe,
        "blocked_7_percent": blocked,
        "max_slippage_pct": MAX_SLIPPAGE_PCT
    }


@app.get("/api/demo-liquidity-check")
def demo_liquidity_check(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    safe = validate_liquidity(
        liquidity_sol=15.0
    )

    blocked = validate_liquidity(
        liquidity_sol=5.0
    )

    return {
        "ok": True,
        "safe_15_sol": safe,
        "blocked_5_sol": blocked,
        "min_liquidity_sol": MIN_LIQUIDITY_SOL
    }


@app.get("/api/demo-execution-check")
def demo_execution_check(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    safe_order = simulate_execution(
        mint="DEMO-EXEC-SAFE",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0
    )

    bad_slippage = simulate_execution(
        mint="DEMO-EXEC-SLIPPAGE",
        expected_price=100.0,
        execution_price=108.0,
        liquidity_sol=15.0,
        amount_usd=5.0
    )

    bad_liquidity = simulate_execution(
        mint="DEMO-EXEC-LIQUIDITY",
        expected_price=100.0,
        execution_price=102.0,
        liquidity_sol=5.0,
        amount_usd=5.0
    )

    return {
        "ok": True,
        "safe_order": safe_order,
        "bad_slippage": bad_slippage,
        "bad_liquidity": bad_liquidity
    }


@app.get("/api/demo-execution-orders")
def demo_execution_orders(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    rows = conn.execute(
        """
        SELECT
            id,
            mint,
            status,
            reason,
            amount_usd,
            expected_price,
            execution_price,
            liquidity_sol

        FROM execution_orders

        ORDER BY id DESC

        LIMIT 20
        """
    ).fetchall()

    conn.close()

    return [
        {
            "id": row[0],
            "mint": row[1],
            "status": row[2],
            "reason": row[3],
            "amount_usd": row[4],
            "expected_price": row[5],
            "execution_price": row[6],
            "liquidity_sol": row[7]
        }

        for row in rows
    ]

@app.post("/api/demo-partial-close")
def demo_partial_close(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "DEMO-PARTIAL-001"

    update_paper_position(
        mint=mint,
        trader="test-partial",
        side="sell",
        market_cap=52.0,
        new_token_balance=0
    )

    return {
        "ok": True,
        "mint": mint,
        "expected": "EXIT"
    }


@app.post("/api/demo-close-old")
def demo_close_old(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    rows = conn.execute(
        """
        SELECT id
        FROM paper_positions
        WHERE status = 'open'
        AND mint LIKE 'DEMO%'
        """
    ).fetchall()

    closed_count = len(rows)

    conn.execute(
        """
        UPDATE paper_positions
        SET
            status = 'closed',
            remaining_pct = 0,
            closed_ts = ?,
            exit_reason = 'DEMO CLEANUP'
        WHERE status = 'open'
        AND mint LIKE 'DEMO%'
        """,
        (
            time.time(),
        )
    )

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "closed_demo_positions": closed_count
    }

@app.get("/api/demo-execution-order-events/{order_id}")
def demo_execution_order_events(
    order_id: int,
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    rows = conn.execute(
        """
        SELECT
            ts,
            status,
            reason

        FROM execution_order_events

        WHERE order_id = ?

        ORDER BY ts ASC
        """,
        (
            order_id,
        )
    ).fetchall()

    conn.close()

    return [
        {
            "ts": row[0],
            "status": row[1],
            "reason": row[2]
        }

        for row in rows
    ]

@app.get("/api/demo-execution-latency/{order_id}")
def demo_execution_latency(
    order_id: int,
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    return get_execution_latency(
        order_id
    )

@app.get("/api/demo-execution-failure")
def demo_execution_failure(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    result = simulate_execution(
        mint="DEMO-EXEC-FAIL",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=True
    )

    return result


@app.get("/api/demo-idempotency-check")
def demo_idempotency_check(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    key = "DEMO-EXEC-IDEMPOTENCY-001"

    first = simulate_execution(
        mint="DEMO-IDEMPOTENT",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        idempotency_key=key
    )

    second = simulate_execution(
        mint="DEMO-IDEMPOTENT",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        idempotency_key=key
    )

    return {
        "ok": True,
        "first": first,
        "second": second
    }

@app.get("/api/demo-idempotency-failed")
def demo_idempotency_failed(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    key = "DEMO-EXEC-IDEMPOTENCY-FAILED-001"

    first = simulate_execution(
        mint="DEMO-IDEMPOTENT-FAILED",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=True,
        idempotency_key=key
    )

    second = simulate_execution(
        mint="DEMO-IDEMPOTENT-FAILED",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=True,
        idempotency_key=key
    )

    return {
        "ok": True,
        "first": first,
        "second": second
    }

@app.get("/api/demo-idempotency-risk-blocked")
def demo_idempotency_risk_blocked(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    key = "DEMO-EXEC-IDEMPOTENCY-RISK-001"

    first = simulate_execution(
        mint="DEMO-IDEMPOTENT-RISK",
        expected_price=100.0,
        execution_price=108.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=False,
        idempotency_key=key
    )

    second = simulate_execution(
        mint="DEMO-IDEMPOTENT-RISK",
        expected_price=100.0,
        execution_price=108.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=False,
        idempotency_key=key
    )

    return {
        "ok": True,
        "first": first,
        "second": second
    }


@app.get("/api/demo-idempotency-sent")
def demo_idempotency_sent(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    key = "DEMO-EXEC-IDEMPOTENCY-SENT-001"

    existing = get_or_create_idempotency(key)

    if not existing["exists"]:

        order_id = create_execution_order(
            mint="DEMO-IDEMPOTENT-SENT",
            side="buy",
            amount_usd=5.0,
            expected_price=100.0,
            execution_price=103.0,
            liquidity_sol=15.0,
            source="demo"
        )

        get_or_create_idempotency(
            key,
            order_id=order_id
        )

        update_execution_order(
            order_id,
            "RISK_CHECKED",
            "RISK_OK"
        )

        update_execution_order(
            order_id,
            "SENT",
            ""
        )

    second = simulate_execution(
        mint="DEMO-IDEMPOTENT-SENT",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=False,
        idempotency_key=key
    )

    return {
        "ok": True,
        "result": second
    }


@app.get("/api/demo-reconcile-sent")
def demo_reconcile_sent(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    result = reconcile_execution_order(
        order_id=17,
        final_status="CONFIRMED",
        reason="SIMULATED_RECONCILIATION_CONFIRMED"
    )

    return {
        "ok": True,
        "result": result
    }


@app.get("/api/demo-idempotency-sent-failed")
def demo_idempotency_sent_failed(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    key = "DEMO-EXEC-IDEMPOTENCY-SENT-FAILED-001"

    existing = get_or_create_idempotency(key)

    if not existing["exists"]:

        order_id = create_execution_order(
            mint="DEMO-IDEMPOTENT-SENT-FAILED",
            side="buy",
            amount_usd=5.0,
            expected_price=100.0,
            execution_price=103.0,
            liquidity_sol=15.0,
            source="demo"
        )

        get_or_create_idempotency(
            key,
            order_id=order_id
        )

        update_execution_order(
            order_id,
            "RISK_CHECKED",
            "RISK_OK"
        )

        update_execution_order(
            order_id,
            "SENT",
            ""
        )

        reconcile_execution_order(
            order_id=order_id,
            final_status="FAILED",
            reason="SIMULATED_RECONCILIATION_FAILED"
        )

    result = simulate_execution(
        mint="DEMO-IDEMPOTENT-SENT-FAILED",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=False,
        idempotency_key=key
    )

    return {
        "ok": True,
        "result": result
    }


@app.get("/api/demo-execution-timeout")
def demo_execution_timeout(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    order_id = create_execution_order(
        mint="DEMO-TIMEOUT",
        side="buy",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        source="demo"
    )

    update_execution_order(
        order_id,
        "RISK_CHECKED",
        "RISK_OK"
    )

    update_execution_order(
        order_id,
        "SENT",
        ""
    )

    conn = db()

    conn.execute(
        """
        UPDATE execution_orders
        SET ts_updated = ?
        WHERE id = ?
        """,
        (
            time.time() - 15,
            order_id
        )
    )

    conn.commit()
    conn.close()

    result = check_execution_timeout(
        order_id
    )

    return {
        "ok": True,
        "result": result
    }

@app.get("/api/demo-pending-reconciliation")
def demo_pending_reconciliation(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    order_id = create_execution_order(
        mint="DEMO-PENDING-RECONCILIATION",
        side="buy",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        source="demo"
    )

    update_execution_order(
        order_id,
        "RISK_CHECKED",
        "RISK_OK"
    )

    update_execution_order(
        order_id,
        "SENT",
        ""
    )

    conn = db()

    conn.execute(
        """
        UPDATE execution_orders
        SET ts_updated = ?
        WHERE id = ?
        """,
        (
            time.time() - 15,
            order_id
        )
    )

    conn.commit()
    conn.close()

    result = mark_order_pending_reconciliation(
        order_id
    )

    return {
        "ok": True,
        "result": result
    }

@app.get("/api/demo-reconcile-pending")
def demo_reconcile_pending(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    result = reconcile_execution_order(
        order_id=20,
        final_status="CONFIRMED",
        reason="SIMULATED_PENDING_RECONCILIATION_CONFIRMED"
    )

    return {
        "ok": True,
        "result": result
    }

@app.get("/api/demo-can-retry")
def demo_can_retry(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    result = can_retry_execution(
        order_id=15
    )

    return {
        "ok": True,
        "result": result
    }

@app.get("/api/demo-cannot-retry-risk")
def demo_cannot_retry_risk(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    result = can_retry_execution(
        order_id=16
    )

    return {
        "ok": True,
        "result": result
    }


@app.get("/api/demo-retry-column")
def demo_retry_column(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    rows = conn.execute(
        """
        PRAGMA table_info(execution_orders)
        """
    ).fetchall()

    conn.close()

    columns = [
        row[1]
        for row in rows
    ]

    return {
        "ok": True,
        "has_retry_count": "retry_count" in columns,
        "columns": columns
    }

@app.get("/api/demo-retry-counter")
def demo_retry_counter(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    order_id = 15

    first = increment_execution_retry(
        order_id
    )

    second = increment_execution_retry(
        order_id
    )

    third = increment_execution_retry(
        order_id
    )

    return {
        "ok": True,
        "first": first,
        "second": second,
        "third": third
    }

@app.get("/api/demo-controlled-retry")
def demo_controlled_retry(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)

    original_order_id = create_execution_order(
        mint="DEMO-CONTROLLED-RETRY",
        side="buy",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        source="demo"
    )

    update_execution_order(
        original_order_id,
        "RISK_CHECKED",
        "RISK_OK"
    )

    update_execution_order(
        original_order_id,
        "SENT",
        ""
    )

    update_execution_order(
        original_order_id,
        "FAILED",
        "SIMULATED_EXECUTION_FAILURE"
    )

    retry_check = can_retry_execution(
        original_order_id
    )

    if not retry_check["ok"]:
        return {
            "ok": False,
            "reason": retry_check["reason"]
        }

    retry_counter = increment_execution_retry(
        original_order_id
    )

    if not retry_counter["ok"]:
        return {
            "ok": False,
            "reason": retry_counter["reason"]
        }

    retry_order = simulate_execution(
    mint="DEMO-CONTROLLED-RETRY",
    expected_price=100.0,
    execution_price=103.0,
    liquidity_sol=15.0,
    amount_usd=5.0,
    force_fail=False,
    idempotency_key="DEMO-CONTROLLED-RETRY-003",
    parent_order_id=original_order_id
)

    return {
        "ok": True,
        "original_order_id": original_order_id,
        "retry_count": retry_counter["retry_count"],
        "retry_result": retry_order
    }

@app.get("/api/demo-parent-order-column")
def demo_parent_order_column(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    rows = conn.execute(
        """
        PRAGMA table_info(execution_orders)
        """
    ).fetchall()

    conn.close()

    columns = [
        row[1]
        for row in rows
    ]

    return {
        "ok": True,
        "has_parent_order_id": "parent_order_id" in columns,
        "columns": columns
    }

@app.get("/api/demo-parent-order-link")
def demo_parent_order_link(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    row = conn.execute(
        """
        SELECT
            id,
            parent_order_id,
            status
        FROM execution_orders
        WHERE id = ?
        LIMIT 1
        """,
        (
            29,
        )
    ).fetchone()

    conn.close()

    if not row:
        return {
            "ok": False,
            "reason": "ORDER_NOT_FOUND"
        }

    return {
        "ok": True,
        "order_id": row[0],
        "parent_order_id": row[1],
        "status": row[2]
    }


@app.get("/api/demo-retry-function")
def demo_retry_function(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    original_order_id = create_execution_order(
        mint="DEMO-RETRY-FUNCTION",
        side="buy",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        source="demo"
    )

    update_execution_order(
        original_order_id,
        "RISK_CHECKED",
        "RISK_OK"
    )

    update_execution_order(
        original_order_id,
        "SENT",
        ""
    )

    update_execution_order(
        original_order_id,
        "FAILED",
        "SIMULATED_EXECUTION_FAILURE"
    )

    result = retry_execution_order(
        order_id=original_order_id,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0
    )

    return {
        "ok": True,
        "original_order_id": original_order_id,
        "result": result
    }


@app.get("/api/demo-retry-limit")
def demo_retry_limit(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    original_order_id = create_execution_order(
        mint="DEMO-RETRY-LIMIT",
        side="buy",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        source="demo"
    )

    update_execution_order(
        original_order_id,
        "RISK_CHECKED",
        "RISK_OK"
    )

    update_execution_order(
        original_order_id,
        "SENT",
        ""
    )

    update_execution_order(
        original_order_id,
        "FAILED",
        "SIMULATED_EXECUTION_FAILURE"
    )

    first = increment_execution_retry(
        original_order_id
    )

    second = increment_execution_retry(
        original_order_id
    )

    third = increment_execution_retry(
        original_order_id
    )

    return {
        "ok": True,
        "original_order_id": original_order_id,
        "first": first,
        "second": second,
        "third": third
    }


@app.get("/api/demo-retry-real-limit")
def demo_retry_real_limit(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    original_order_id = create_execution_order(
        mint="DEMO-RETRY-REAL-LIMIT",
        side="buy",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        source="demo"
    )

    update_execution_order(
        original_order_id,
        "RISK_CHECKED",
        "RISK_OK"
    )

    update_execution_order(
        original_order_id,
        "SENT",
        ""
    )

    update_execution_order(
        original_order_id,
        "FAILED",
        "SIMULATED_EXECUTION_FAILURE"
    )

    first = retry_execution_order(
        order_id=original_order_id,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=True
    )

    update_execution_order(
        original_order_id,
        "FAILED",
        "SIMULATED_EXECUTION_FAILURE"
    )

    second = retry_execution_order(
        order_id=original_order_id,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=True
    )

    update_execution_order(
        original_order_id,
        "FAILED",
        "SIMULATED_EXECUTION_FAILURE"
    )

    third = retry_execution_order(
        order_id=original_order_id,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=True
    )

    return {
        "ok": True,
        "original_order_id": original_order_id,
        "first": first,
        "second": second,
        "third": third
    }


@app.get("/api/demo-restart-recovery")
def demo_restart_recovery(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    order_id = 31

    conn = db()

    row = conn.execute(
        """
        SELECT
            id,
            status,
            retry_count,
            parent_order_id
        FROM execution_orders
        WHERE id = ?
        LIMIT 1
        """,
        (
            order_id,
        )
    ).fetchone()

    conn.close()

    if not row:
        return {
            "ok": False,
            "reason": "ORDER_NOT_FOUND"
        }

    return {
        "ok": True,
        "order_id": row[0],
        "status": row[1],
        "retry_count": row[2],
        "parent_order_id": row[3]
    }

@app.get("/api/demo-concurrent-idempotency")
def demo_concurrent_idempotency(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    key = f"DEMO-CONCURRENT-{time.time_ns()}"

    results = []

    def worker():

        try:

            result = simulate_execution(
                mint="DEMO-CONCURRENT-IDEMPOTENCY",
                expected_price=100.0,
                execution_price=103.0,
                liquidity_sol=15.0,
                amount_usd=5.0,
                force_fail=False,
                idempotency_key=key
            )

            results.append(
                {
                    "type": "RESULT",
                    "data": result
                }
            )

        except Exception as ex:

            results.append(
                {
                    "type": "ERROR",
                    "error": repr(ex)
                }
            )

    import threading

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    return {
        "ok": True,
        "key": key,
        "results": results
    }

@app.get("/api/demo-live-guard")
def demo_live_guard(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()
    require_live_trading()

    return {
        "ok": True,
        "result": "LIVE_TRADING_ALLOWED"
    }


@app.get("/api/demo-mode-column")
def demo_mode_column(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    rows = conn.execute(
        """
        PRAGMA table_info(execution_orders)
        """
    ).fetchall()

    conn.close()

    columns = [
        row[1]
        for row in rows
    ]

    return {
        "ok": True,
        "has_mode": "mode" in columns,
        "columns": columns
    }

@app.get("/api/demo-mode-save")
def demo_mode_save(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    result = simulate_execution(
        mint="DEMO-MODE-SAVE",
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0,
        force_fail=False,
        idempotency_key=f"DEMO-MODE-{time.time_ns()}",
        mode="demo"
    )

    order_id = result["order_id"]

    conn = db()

    row = conn.execute(
        """
        SELECT
            id,
            mode,
            status
        FROM execution_orders
        WHERE id = ?
        LIMIT 1
        """,
        (
            order_id,
        )
    ).fetchone()

    conn.close()

    return {
        "ok": True,
        "order_id": row[0],
        "mode": row[1],
        "status": row[2]
    }

@app.get("/api/demo-retry-mode")
def demo_retry_mode(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    original_order_id = create_execution_order(
        mint="DEMO-RETRY-MODE",
        side="buy",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        source="demo",
        mode="demo"
    )

    update_execution_order(
        original_order_id,
        "FAILED",
        "SIMULATED_EXECUTION_FAILURE"
    )

    result = retry_execution_order(
        order_id=original_order_id,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        amount_usd=5.0
    )

    retry_order_id = result["retry_result"]["order_id"]

    conn = db()

    row = conn.execute(
        """
        SELECT
            id,
            parent_order_id,
            mode,
            status
        FROM execution_orders
        WHERE id = ?
        LIMIT 1
        """,
        (
            retry_order_id,
        )
    ).fetchone()

    conn.close()

    return {
        "ok": True,
        "original_order_id": original_order_id,
        "retry_order_id": row[0],
        "parent_order_id": row[1],
        "mode": row[2],
        "status": row[3]
    }


@app.get("/api/demo-paper-mode-column")
def demo_paper_mode_column(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    rows = conn.execute(
        """
        PRAGMA table_info(paper_positions)
        """
    ).fetchall()

    conn.close()

    columns = [
        row[1]
        for row in rows
    ]

    return {
        "ok": True,
        "has_mode": "mode" in columns,
        "columns": columns
    }

@app.get("/api/demo-paper-mode-save")
def demo_paper_mode_save(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = f"DEMO-PAPER-MODE-{time.time_ns()}"

    open_paper_position(
        mint=mint,
        trader="demo-mode-test",
        market_cap=50.0,
        score=90,
        decision="COPY",
        mode="demo"
    )

    conn = db()

    row = conn.execute(
        """
        SELECT
            mint,
            mode,
            status
        FROM paper_positions
        WHERE mint = ?
        LIMIT 1
        """,
        (
            mint,
        )
    ).fetchone()

    conn.close()

    if not row:
        return {
            "ok": False,
            "reason": "POSITION_NOT_CREATED"
        }

    return {
        "ok": True,
        "mint": row[0],
        "mode": row[1],
        "status": row[2]
    }

@app.get("/api/demo-mode-isolation")
def demo_mode_isolation(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    demo_mint = f"DEMO-ISO-{time.time_ns()}"
    paper_mint = f"PAPER-ISO-{time.time_ns()}"

    open_paper_position(
        mint=demo_mint,
        trader="demo-isolation",
        market_cap=50.0,
        score=90,
        decision="COPY",
        mode="demo"
    )

    open_paper_position(
        mint=paper_mint,
        trader="paper-isolation",
        market_cap=50.0,
        score=90,
        decision="COPY",
        mode="paper"
    )

    conn = db()

    demo_row = conn.execute(
        """
        SELECT mint, mode, status
        FROM paper_positions
        WHERE mint = ?
        LIMIT 1
        """,
        (demo_mint,)
    ).fetchone()

    paper_row = conn.execute(
        """
        SELECT mint, mode, status
        FROM paper_positions
        WHERE mint = ?
        LIMIT 1
        """,
        (paper_mint,)
    ).fetchone()

    conn.close()

    return {
        "ok": True,
        "demo_created": demo_row is not None,
        "paper_created": paper_row is not None,
        "demo": demo_row,
        "paper": paper_row
    }

@app.get("/api/demo-daily-pnl-isolation")
def demo_daily_pnl_isolation(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    conn = db()

    now = time.time()

    demo_mint = f"DEMO-PNL-{time.time_ns()}"
    paper_mint = f"PAPER-PNL-{time.time_ns()}"

    conn.execute(
        """
        INSERT INTO paper_positions(
            opened_ts,
            mint,
            trigger_traders,
            entry_mc,
            stake_usd,
            status,
            pnl_usd,
            decision,
            score,
            origin_trader,
            current_mc,
            remaining_pct,
            realized_pnl_usd,
            unrealized_pnl_usd,
            last_action,
            tp_stage,
            closed_ts,
            exit_mc,
            exit_reason,
            mode
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            now,
            demo_mint,
            json.dumps(["demo-pnl"]),
            50.0,
            5.0,
            "closed",
            0.0,
            "COPY",
            90,
            "demo-pnl",
            50.0,
            0.0,
            -10.0,
            0.0,
            "EXIT",
            0,
            now,
            40.0,
            "DEMO LOSS",
            "demo"
        )
    )

    conn.execute(
        """
        INSERT INTO paper_positions(
            opened_ts,
            mint,
            trigger_traders,
            entry_mc,
            stake_usd,
            status,
            pnl_usd,
            decision,
            score,
            origin_trader,
            current_mc,
            remaining_pct,
            realized_pnl_usd,
            unrealized_pnl_usd,
            last_action,
            tp_stage,
            closed_ts,
            exit_mc,
            exit_reason,
            mode
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            now,
            paper_mint,
            json.dumps(["paper-pnl"]),
            50.0,
            5.0,
            "closed",
            0.0,
            "COPY",
            90,
            "paper-pnl",
            50.0,
            0.0,
            2.0,
            0.0,
            "EXIT",
            0,
            now,
            55.0,
            "PAPER PROFIT",
            "paper"
        )
    )

    conn.commit()
    conn.close()

    demo_pnl = get_daily_realized_pnl(
        mode="demo"
    )

    paper_pnl = get_daily_realized_pnl(
        mode="paper"
    )

    return {
        "ok": True,
        "demo_pnl": demo_pnl,
        "paper_pnl": paper_pnl
    }    

@app.get("/api/demo-risk-mode-isolation")
def demo_risk_mode_isolation(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    demo_risk = risk_check(
        mint="DEMO-RISK-ISOLATION",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        mode="demo"
    )

    paper_risk = risk_check(
        mint="PAPER-RISK-ISOLATION",
        amount_usd=5.0,
        expected_price=100.0,
        execution_price=103.0,
        liquidity_sol=15.0,
        mode="paper"
    )

    return {
        "ok": True,
        "demo_risk": demo_risk,
        "paper_risk": paper_risk
    }


@app.post("/api/demo-tp1")
def demo_tp1(
    x_app_token: str = Header(default="")
):

    auth(x_app_token)
    require_debug_mode()

    mint = "9m2V3MtBw6nbcXVWLVose3HcpN6wKryr4Egx8Dsypump"

    entry_mc = 45.85
    tp1_mc = entry_mc * 1.30

    update_paper_position(
        mint=mint,
        trader="test-market",
        side="buy",
        market_cap=tp1_mc,
        new_token_balance=1
    )

    return {
        "ok": True,
        "mint": mint,
        "market_cap": tp1_mc,
        "expected": "TAKE PROFIT +25%"
    }
# =========================================================
# FRONTEND
# =========================================================

@app.get("/")
def home():

    return FileResponse(
        BASE
        /
        "static"
        /
        "index.html"
    )


app.mount(
    "/static",

    StaticFiles(
        directory=
            BASE
            /
            "static"
    ),

    name="static"
)