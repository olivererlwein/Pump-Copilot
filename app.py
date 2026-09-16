import os
import json
import time
import asyncio
import math
import statistics
import collections
import secrets
import sqlite3
import random
import uuid
import decimal
import threading

from pathlib import Path
from urllib.request import Request, urlopen

# Con alias: `Request` a secas pisaría `urllib.request.Request`, que este
# módulo usa para todas sus llamadas HTTP salientes.
from fastapi import FastAPI, Header, HTTPException
from fastapi import Request as FastAPIRequest
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from dotenv import load_dotenv

import websockets

from shadow_model import ShadowLogisticModel
from helius_credit_usage import HeliusCreditUsageError, fetch_helius_credit_usage
from helius_webhook_sync import (
    fetch_helius_webhook,
    normalize_addresses,
    plan_webhook_address_sync,
    update_helius_webhook_addresses,
    webhook_account_addresses,
)
from solana_receipts import parse_buy_receipt, parse_sell_receipt
from solana_rpc_fallback import (
    PUMP_AMM_PROGRAM_ID,
    PUMP_PROGRAM_ID,
    fetch_confirmed_transaction,
    fetch_signatures_for_address,
    parse_watched_wallet_pump_events,
    parse_tracked_token_pump_events,
)


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

DISCORD_ALERT_WEBHOOK_URL = os.getenv(
    "DISCORD_ALERT_WEBHOOK_URL",
    ""
)

PUMPPORTAL_WALLET_ADDRESS = os.getenv(
    "PUMPPORTAL_WALLET_ADDRESS",
    "nXKa9jR8rPvoAAr4c82awiznLDFjSqyG9zk6ivBMcP6"
)

PUMPPORTAL_LOW_BALANCE_SOL = float(
    os.getenv(
        "PUMPPORTAL_LOW_BALANCE_SOL",
        "0.025"
    )
)

PUMPPORTAL_BALANCE_CHECK_SECONDS = max(
    60,
    int(
        os.getenv(
            "PUMPPORTAL_BALANCE_CHECK_SECONDS",
            "300"
        )
    )
)

SOLANA_RPC_URL = os.getenv(
    "SOLANA_RPC_URL",
    "https://api.mainnet-beta.solana.com"
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
# CALIDAD DEL TRADER
# =========================================================
# Todos los traders parten del mismo prior neutral. Cuando hay suficientes
# resultados completados, la calidad refleja TP25 antes de SL10.

TRADER_QUALITY_NEUTRAL = 15
TRADER_QUALITY_PRIOR_SUCCESSES = 8
TRADER_QUALITY_PRIOR_FAILURES = 12

TRADER_DYNAMIC_QUALITY_ENABLED = os.getenv(
    "TRADER_DYNAMIC_QUALITY_ENABLED",
    "true"
).lower() == "true"

TRADER_QUALITY_MIN_SAMPLES = max(
    1,
    int(os.getenv("TRADER_QUALITY_MIN_SAMPLES", "30"))
)

TRADER_QUALITY_MIN = 5
TRADER_QUALITY_MAX = 30

# Un outcome es evidencia utilizable cuando su desenlace es decidible:
# vimos disparar el TP25, el SL10, o completamos la ventana de observación.
#
# 'expired' NO significa "salió mal": significa que perdimos la observación
# de precio a los 20 minutos, normalmente porque el token dejó de operar. Si
# alcanzamos a ver el TP25 antes de perderlo de vista, ese resultado es real
# y descartarlo tira evidencia. Solo se excluyen los verdaderamente
# indecidibles: sin ninguno de los dos timestamps y sin ventana completa.
#
# Los outcomes 'active' quedan fuera aunque ya tengan un timestamp: todavía
# están mutando y se resuelven solos en ~20 minutos. La evidencia que se
# recupera acá son los 'expired' que alcanzaron a decidirse.
DECIDABLE_OUTCOME_SQL = """(
        status = 'completed'
        OR (
            status = 'expired'
            AND (
                tp25_ts IS NOT NULL
                OR sl10_ts IS NOT NULL
            )
        )
    )"""

# =========================================================
# PERFIL INTEGRAL DE CALIDAD (SHADOW)
# =========================================================
# Evaluación multidimensional y observacional. No alimenta score_trader(),
# decision_from_score() ni ninguna ruta de ejecución: solo se expone para
# revisión humana hasta que sea validada y promovida explícitamente.

TRADER_PROFILE_UNRATED_LABEL = "Sin calificar"

# Ciclos compra -> ventas mínimos antes de publicar calidad de salida.
TRADER_PROFILE_MIN_CYCLES = max(
    1,
    int(os.getenv("TRADER_PROFILE_MIN_CYCLES", "10"))
)

# Mitad de vida (días) del decaimiento por recencia.
TRADER_PROFILE_RECENCY_HALFLIFE_DAYS = max(
    0.5,
    float(os.getenv("TRADER_PROFILE_RECENCY_HALFLIFE_DAYS", "14"))
)

# Muestras mínimas por mitad temporal para medir consistencia.
TRADER_PROFILE_MIN_HALF_SAMPLES = 5

# Pesos del score integral. Solo se usan los componentes disponibles y los
# pesos se renormalizan sobre esos, para no penalizar datos faltantes con 0.
TRADER_PROFILE_WEIGHTS = {
    "entry_quality": 0.34,
    "returns": 0.18,
    "exit_quality": 0.18,
    "consistency": 0.12,
    "diversification": 0.08,
    "recency": 0.08,
    "copyability": 0.02,
}

TRUSTED_TRADERS = {
    "marcell",
    "hdegroot",
    "gr3gor14n",
    "epicsealdarkeye",
    "supermandev",
}

OBSERVE_TRADERS = {
    "ily",
    "sapphy",
    "FlippingProfits",
}

TRACKED_TOKENS = set()
SUBSCRIBED_TOKENS = set()
TOKENS_TO_UNSUBSCRIBE = set()
LAST_TOKEN_PRICE = {}
LAST_STREAM_MESSAGE_TS = 0.0
LAST_STREAM_EVENT_TS = 0.0
LAST_PUMPPORTAL_MESSAGE = ""
STREAM_CONNECTED = False
STREAM_LAST_ERROR = ""
STREAM_ALERT_ACTIVE = False
STREAM_FAILURE_STARTED_TS = 0.0
PUMPPORTAL_WALLET_BALANCE_SOL = None
PUMPPORTAL_BALANCE_CHECKED_TS = 0.0
PUMPPORTAL_BALANCE_LAST_ERROR = ""
PUMPPORTAL_BALANCE_ALERT_ACTIVE = False

# Evita procesar dos veces la misma transacción de PumpPortal
KILL_SWITCH = False

DEBUG_MODE = os.getenv(
    "DEBUG_MODE",
    "false"
).lower() == "true"


def environment_flag(name, default=False):
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() == "true"

LIVE_TRADING = os.getenv(
    "LIVE_TRADING",
    "false"
).lower() == "true"

LIVE_EXECUTION_IMPLEMENTED = environment_flag(
    "LIVE_EXECUTION_IMPLEMENTED",
)
from helius_standard_wss import (
    build_helius_standard_wss_url,
    build_logs_subscribe_request,
    build_logs_unsubscribe_request,
    decode_wss_message,
    invokes_program,
    parse_logs_notification,
    select_tracked_tokens,
    subscription_confirmation,
)

LIVE_CANARY_ENABLED = os.getenv(
    "LIVE_CANARY_ENABLED",
    "false",
).lower() == "true"

LIVE_APPROVED_MODEL_VERSION = os.getenv(
    "LIVE_APPROVED_MODEL_VERSION",
    "",
).strip()

try:
    LIVE_CANARY_MAX_BUY_USD = float(
        os.getenv("LIVE_CANARY_MAX_BUY_USD", "0")
    )
except (TypeError, ValueError):
    LIVE_CANARY_MAX_BUY_USD = 0.0

try:
    LIVE_CANARY_MAX_BUYS_PER_DAY = int(
        os.getenv("LIVE_CANARY_MAX_BUYS_PER_DAY", "0")
    )
except (TypeError, ValueError):
    LIVE_CANARY_MAX_BUYS_PER_DAY = 0

try:
    LIVE_CANARY_MAX_DAILY_NOTIONAL_USD = float(
        os.getenv("LIVE_CANARY_MAX_DAILY_NOTIONAL_USD", "0")
    )
except (TypeError, ValueError):
    LIVE_CANARY_MAX_DAILY_NOTIONAL_USD = 0.0

LIVE_CANARY_ALLOWED_TRADERS = {
    trader.strip()
    for trader in os.getenv("LIVE_CANARY_ALLOWED_TRADERS", "").split(",")
    if trader.strip()
}

# Estos topes no son configurables: evitan convertir accidentalmente el
# canary en operación normal mediante variables de Railway demasiado amplias.
LIVE_CANARY_HARD_MAX_BUY_USD = 1.0
LIVE_CANARY_HARD_MAX_BUYS_PER_DAY = 3
LIVE_CANARY_HARD_MAX_BUYS_PER_TRADER_PER_DAY = 1
LIVE_CANARY_HARD_MAX_DAILY_NOTIONAL_USD = 3.0
LIVE_CANARY_REVIEWED_MODEL_VERSION = ""

LIVE_BUYS_ENABLED = os.getenv(
    "LIVE_BUYS_ENABLED",
    "false"
).lower() == "true"

LIVE_SELLS_ENABLED = os.getenv(
    "LIVE_SELLS_ENABLED",
    "false"
).lower() == "true"

try:
    LIVE_BUY_USD = float(
        os.getenv("LIVE_BUY_USD", "0")
    )
except (TypeError, ValueError):
    LIVE_BUY_USD = 0.0

EXECUTION_PROVIDER = os.getenv(
    "EXECUTION_PROVIDER",
    "simulation"
).strip().lower()

PUMPPORTAL_ALLOWED_POOLS = {
    "auto",
    "pump",
    "raydium",
    "pump-amm",
    "launchlab",
    "raydium-cpmm",
    "bonk",
}
SOL_PRICE_MAX_AGE_SECONDS = 30.0
MAX_PRIORITY_FEE_SOL = 0.001
SOL_USD_PRICE_URL = (
    "https://api.coinbase.com/v2/prices/SOL-USD/spot"
)

PUMPPORTAL_TRADING_WALLET_ADDRESS = os.getenv(
    "PUMPPORTAL_TRADING_WALLET_ADDRESS", ""
).strip()

MAX_POSITION_USD = 5.0
MAX_DAILY_LOSS_USD = 5.0
MAX_SLIPPAGE_PCT = 5.0
MIN_LIQUIDITY_SOL = 10.0

EXECUTION_TIMEOUT_SECONDS = 10
MAX_EXECUTION_RETRIES = 2
EXECUTION_RECONCILIATION_SECONDS = 5

STREAM_INACTIVITY_TIMEOUT = 120

# Una wallet vigilada puede dejar de entregar eventos mientras la conexión
# sigue sana y otras wallets siguen llegando. El watchdog del stream no ve ese
# caso porque el stream nunca se cae. Estos controles lo hacen visible.
WATCHED_WALLET_SILENCE_SECONDS = max(
    3600,
    int(os.getenv("WATCHED_WALLET_SILENCE_SECONDS", "86400"))
)

WATCHED_WALLET_CHECK_SECONDS = max(
    60,
    int(os.getenv("WATCHED_WALLET_CHECK_SECONDS", "900"))
)

# Reafirmar la suscripción de cuentas cada tanto: si el proveedor la descarta
# en silencio, se recupera sola sin esperar a una reconexión.
WATCHED_RESUBSCRIBE_SECONDS = max(
    60,
    int(os.getenv("WATCHED_RESUBSCRIBE_SECONDS", "1800"))
)

WATCHED_WALLET_ALERTS = set()

# =========================================================
# WEBHOOK DE HELIUS
# =========================================================
# Registra lo que llega para comparar cobertura y latencia. El consumidor que
# puede alimentar señales y paper tiene un interruptor independiente y nunca
# habilita ejecución real desde Helius.
HELIUS_WEBHOOK_ENABLED = os.getenv(
    "HELIUS_WEBHOOK_ENABLED",
    "false",
).lower() == "true"

# Secreto compartido que Helius envía en la cabecera Authorization. Sin esto
# configurado el endpoint no acepta nada: es una ruta pública que escribe en
# la base.
HELIUS_WEBHOOK_SECRET = os.getenv("HELIUS_WEBHOOK_SECRET", "")

HELIUS_WEBHOOK_MAX_TRANSACTIONS = max(
    1,
    int(os.getenv("HELIUS_WEBHOOK_MAX_TRANSACTIONS", "200")),
)

# Cuántos payloads sin parsear se guardan enteros. Sin una muestra real no hay
# forma de distinguir "no era una operación Pump" de "el formato no es el que
# el parser espera", que se ven exactamente igual desde afuera.
HELIUS_WEBHOOK_RAW_SAMPLES = max(
    0,
    int(os.getenv("HELIUS_WEBHOOK_RAW_SAMPLES", "5")),
)

HELIUS_WEBHOOK_RAW_SAMPLE_CHARS = 20000

# Sincroniza los tokens que el pipeline necesita observar con el webhook de
# Helius. Los dos interruptores se separan para poder validar el plan remoto en
# producción antes de autorizar PUTs que cuestan créditos y cambian cobertura.
HELIUS_WEBHOOK_SYNC_ENABLED = os.getenv(
    "HELIUS_WEBHOOK_SYNC_ENABLED",
    "false",
).lower() == "true"

HELIUS_WEBHOOK_SYNC_APPLY = os.getenv(
    "HELIUS_WEBHOOK_SYNC_APPLY",
    "false",
).lower() == "true"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_PROJECT_ID = os.getenv("HELIUS_PROJECT_ID", "").strip()
HELIUS_WEBHOOK_ID = os.getenv("HELIUS_WEBHOOK_ID", "").strip()

HELIUS_WEBHOOK_SYNC_POLL_SECONDS = max(
    1,
    int(os.getenv("HELIUS_WEBHOOK_SYNC_POLL_SECONDS", "5")),
)

HELIUS_WEBHOOK_SYNC_AUDIT_SECONDS = max(
    60,
    int(os.getenv("HELIUS_WEBHOOK_SYNC_AUDIT_SECONDS", "900")),
)

HELIUS_WEBHOOK_SYNC_ERROR_RETRY_SECONDS = max(
    30,
    int(os.getenv("HELIUS_WEBHOOK_SYNC_ERROR_RETRY_SECONDS", "60")),
)

HELIUS_WEBHOOK_SYNC_RATE_LIMIT_RETRY_SECONDS = max(
    HELIUS_WEBHOOK_SYNC_ERROR_RETRY_SECONDS,
    int(os.getenv("HELIUS_WEBHOOK_SYNC_RATE_LIMIT_RETRY_SECONDS", "300")),
)

HELIUS_WEBHOOK_SYNC_TIMEOUT_SECONDS = max(
    1,
    int(os.getenv("HELIUS_WEBHOOK_SYNC_TIMEOUT_SECONDS", "15")),
)

HELIUS_WEBHOOK_SYNC_LOCK = threading.Lock()
HELIUS_WEBHOOK_SYNC_ALERT_ACTIVE = False

# Piloto de bajo costo: recibe solo logs de las wallets vigiladas y pide la
# transacción completa únicamente cuando los logs invocan Pump/Pump AMM.
# `APPLY` queda separado para medir cobertura y consumo antes de alimentar el
# inbox que toma decisiones.
HELIUS_STANDARD_WSS_ENABLED = os.getenv(
    "HELIUS_STANDARD_WSS_ENABLED", "false"
).lower() == "true"
HELIUS_STANDARD_WSS_APPLY = os.getenv(
    "HELIUS_STANDARD_WSS_APPLY", "false"
).lower() == "true"
HELIUS_STANDARD_WSS_URL = os.getenv("HELIUS_STANDARD_WSS_URL", "").strip()
HELIUS_STANDARD_WSS_RECONNECT_SECONDS = max(
    1, int(os.getenv("HELIUS_STANDARD_WSS_RECONNECT_SECONDS", "3"))
)
HELIUS_STANDARD_WSS_RATE_LIMIT_RETRY_SECONDS = max(
    HELIUS_STANDARD_WSS_RECONNECT_SECONDS,
    int(os.getenv("HELIUS_STANDARD_WSS_RATE_LIMIT_RETRY_SECONDS", "300")),
)
HELIUS_STANDARD_WSS_FETCH_RETRIES = max(
    1, min(5, int(os.getenv("HELIUS_STANDARD_WSS_FETCH_RETRIES", "3")))
)
HELIUS_STANDARD_WSS_MAX_IN_FLIGHT = max(
    1, min(20, int(os.getenv("HELIUS_STANDARD_WSS_MAX_IN_FLIGHT", "4")))
)
HELIUS_STANDARD_WSS_FETCH_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("HELIUS_STANDARD_WSS_FETCH_INTERVAL_SECONDS", "0.15")),
)
HELIUS_STANDARD_WSS_MAX_PENDING = max(
    10, min(5000, int(os.getenv("HELIUS_STANDARD_WSS_MAX_PENDING", "500")))
)
HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED = os.getenv(
    "HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED", "false"
).lower() == "true"
HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS = max(
    1, min(100, int(os.getenv("HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS", "50")))
)
HELIUS_STANDARD_WSS_TOKEN_POLL_SECONDS = max(
    1, min(60, int(os.getenv("HELIUS_STANDARD_WSS_TOKEN_POLL_SECONDS", "5")))
)
HELIUS_STANDARD_WSS_STATE_LOCK = threading.Lock()
HELIUS_STANDARD_WSS_STATE = {
    "connected": False,
    "subscriptions": 0,
    "tracked_token_subscriptions": 0,
    "tracked_tokens_desired": 0,
    "tracked_tokens_omitted": 0,
    "pending_fetches": 0,
    "last_connect_ts": None,
    "last_message_ts": None,
    "last_pump_log_ts": None,
    "last_success_ts": None,
    "last_error": None,
    "reconnects": 0,
    "retry_seconds": None,
    "next_retry_ts": None,
}

MARKET_EVENT_INBOX_ACTIVATION_STATE_KEY = (
    "market_event_inbox_processing_activation_ts"
)

# Primera etapa del consumidor del inbox: solo reconstruye y valida eventos.
# No llama al router ni produce efectos de trading. Se habilita por separado
# después de desplegar y observar la migración.
MARKET_EVENT_INBOX_VALIDATION_ENABLED = os.getenv(
    "MARKET_EVENT_INBOX_VALIDATION_ENABLED",
    "false",
).lower() == "true"

MARKET_EVENT_INBOX_VALIDATION_BATCH_SIZE = max(
    1,
    min(
        200,
        int(os.getenv("MARKET_EVENT_INBOX_VALIDATION_BATCH_SIZE", "25")),
    ),
)

MARKET_EVENT_INBOX_VALIDATION_POLL_SECONDS = max(
    1,
    int(os.getenv("MARKET_EVENT_INBOX_VALIDATION_POLL_SECONDS", "5")),
)

MARKET_EVENT_INBOX_VALIDATION_LEASE_SECONDS = max(
    30,
    int(os.getenv("MARKET_EVENT_INBOX_VALIDATION_LEASE_SECONDS", "120")),
)

# Segunda etapa: aplica al pipeline los eventos ya validados. Arranca apagada
# y fija una frontera persistente al habilitarse para no reproducir historia.
MARKET_EVENT_INBOX_CONSUMER_ENABLED = os.getenv(
    "MARKET_EVENT_INBOX_CONSUMER_ENABLED",
    "false",
).lower() == "true"

MARKET_EVENT_INBOX_CONSUMER_BATCH_SIZE = max(
    1,
    min(
        200,
        int(os.getenv("MARKET_EVENT_INBOX_CONSUMER_BATCH_SIZE", "25")),
    ),
)

MARKET_EVENT_INBOX_CONSUMER_POLL_SECONDS = max(
    1,
    int(os.getenv("MARKET_EVENT_INBOX_CONSUMER_POLL_SECONDS", "5")),
)

MARKET_EVENT_INBOX_CONSUMER_LEASE_SECONDS = max(
    30,
    int(os.getenv("MARKET_EVENT_INBOX_CONSUMER_LEASE_SECONDS", "120")),
)

# Fallback observacional para auditar los eventos de cuenta que PumpPortal no
# entrega. No entra a save_trade(), scoring, señales ni ejecución hasta que sus
# resultados hayan sido comparados y promovidos explícitamente.
RPC_FALLBACK_SHADOW_ENABLED = os.getenv(
    "RPC_FALLBACK_SHADOW_ENABLED",
    "false",
).lower() == "true"

RPC_FALLBACK_POLL_SECONDS = max(
    30,
    int(os.getenv("RPC_FALLBACK_POLL_SECONDS", "90")),
)

RPC_FALLBACK_SIGNATURE_LIMIT = max(
    1,
    min(1000, int(os.getenv("RPC_FALLBACK_SIGNATURE_LIMIT", "100"))),
)

RPC_FALLBACK_MAX_TRANSACTIONS_PER_POLL = max(
    1,
    int(os.getenv("RPC_FALLBACK_MAX_TRANSACTIONS_PER_POLL", "30")),
)

RPC_FALLBACK_GRACE_SECONDS = max(
    10,
    int(os.getenv("RPC_FALLBACK_GRACE_SECONDS", "45")),
)

RPC_FALLBACK_LAST_POLL_TS = 0.0
RPC_FALLBACK_LAST_SUCCESS_TS = 0.0
RPC_FALLBACK_LAST_ERROR = ""
RPC_FALLBACK_SCANNED_SIGNATURES = 0
RPC_FALLBACK_PARSED_EVENTS = 0
RPC_FALLBACK_SATURATED_WALLETS = []

# Historial corto de mensajes del proveedor. Antes solo se guardaba el último,
# así que la respuesta a subscribeAccountTrade se perdía apenas llegaba
# cualquier otro mensaje y no había forma de saber si la suscripción de
# cuentas fue aceptada.
PUMPPORTAL_MESSAGE_LOG = collections.deque(maxlen=50)

DATA_VERSION = 2
PUMP_TOKEN_SUPPLY = 1_000_000_000.0

SHADOW_MODE_ENABLED = os.getenv(
    "SHADOW_MODE_ENABLED",
    "true"
).lower() == "true"

SHADOW_MODEL_PATH = Path(
    os.getenv(
        "SHADOW_MODEL_PATH",
        str(BASE / "models" / "baseline_v2_shadow.json")
    )
)

SHADOW_CHALLENGER_MODEL_PATH = Path(
    os.getenv(
        "SHADOW_CHALLENGER_MODEL_PATH",
        str(BASE / "models" / "baseline_v2_challenger_shadow.json")
    )
)

SHADOW_REVIEW_MIN_COMPLETED = max(
    1,
    int(os.getenv("SHADOW_REVIEW_MIN_COMPLETED", "100"))
)

SEEN_EVENT_IDS = set()
FORCE_STREAM_ERROR = False
SHADOW_MODEL = None
SHADOW_MODEL_LAST_ERROR = ""
SHADOW_CHALLENGER_MODEL = None
SHADOW_CHALLENGER_LAST_ERROR = ""


# =========================================================
# BASE DE DATOS
# =========================================================

def db():

    conn = sqlite3.connect(
        DB,
        timeout=30.0,
    )

    conn.execute(
        "PRAGMA busy_timeout = 30000"
    )

    conn.execute(
        "PRAGMA synchronous = NORMAL"
    )

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

    # Identidad completa para transportes capaces de entregar más de una
    # operación Pump dentro de una misma transacción. La tabla anterior se
    # conserva para una reversión segura y se migra como índice 0.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_market_events(
            event_id TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            ts REAL,
            source TEXT DEFAULT 'live'
        )
        """
    )

    # La clave primaria es `event_id`; las consultas que preguntan "¿algún
    # transporte aplicó esta firma?" —stats del webhook, fallback RPC— buscan
    # por firma. Sin este índice cada una recorre la tabla entera por fila
    # evaluada, y la tabla crece con cada evento del stream.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_processed_market_events_signature
        ON processed_market_events(signature)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_trades_signature
        ON trades(signature)
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

    # Piloto del webhook de Helius: qué llegó por push y con cuánto retraso.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS helius_webhook_events(
            signature TEXT PRIMARY KEY,
            wallet TEXT,
            trader TEXT,
            block_time REAL,
            received_ts REAL,
            parsed INTEGER DEFAULT 0,
            side TEXT,
            mint TEXT,
            pool TEXT,
            raw_sample TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_helius_webhook_parsed_received "
        "ON helius_webhook_events(parsed, received_ts DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS helius_webhook_wallet_observations(
            signature TEXT NOT NULL,
            wallet TEXT NOT NULL,
            received_ts REAL NOT NULL,
            parsed INTEGER NOT NULL,
            pump_program_in_accounts INTEGER,
            PRIMARY KEY(signature, wallet)
        )
        """
    )
    wallet_observation_columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(helius_webhook_wallet_observations)"
        )
    }
    if "pump_program_in_accounts" not in wallet_observation_columns:
        conn.execute(
            "ALTER TABLE helius_webhook_wallet_observations "
            "ADD COLUMN pump_program_in_accounts INTEGER"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_helius_wallet_observations_received "
        "ON helius_webhook_wallet_observations(received_ts, wallet)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS helius_standard_wss_notifications(
            signature TEXT NOT NULL,
            wallet TEXT NOT NULL,
            received_ts REAL NOT NULL,
            slot INTEGER,
            failed INTEGER NOT NULL,
            pump_logs INTEGER NOT NULL,
            message_bytes INTEGER NOT NULL,
            subject_type TEXT NOT NULL DEFAULT 'wallet',
            PRIMARY KEY(signature, wallet)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_helius_wss_notifications_received "
        "ON helius_standard_wss_notifications(received_ts)"
    )
    wss_notification_columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(helius_standard_wss_notifications)"
        )
    }
    if "subject_type" not in wss_notification_columns:
        conn.execute(
            "ALTER TABLE helius_standard_wss_notifications "
            "ADD COLUMN subject_type TEXT NOT NULL DEFAULT 'wallet'"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS helius_standard_wss_transactions(
            signature TEXT PRIMARY KEY,
            first_received_ts REAL NOT NULL,
            fetched_ts REAL,
            block_time REAL,
            status TEXT NOT NULL,
            fetch_attempts INTEGER NOT NULL DEFAULT 0,
            parsed_events INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        )
        """
    )

    # Eventos normalizados preservados antes de activar cualquier efecto. Una
    # transacción puede contener más de una operación Pump válida.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_event_inbox(
            signature TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            event_index_scheme TEXT NOT NULL DEFAULT 'log-v1',
            source TEXT NOT NULL,
            wallet TEXT,
            trader TEXT,
            mint TEXT,
            side TEXT,
            pool TEXT,
            block_time REAL,
            block_event_ts REAL,
            received_ts REAL NOT NULL,
            event_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'observed',
            attempts INTEGER NOT NULL DEFAULT 0,
            claim_token TEXT,
            claimed_ts REAL,
            processed_ts REAL,
            last_error TEXT,
            PRIMARY KEY(signature, event_index)
        )
        """
    )

    try:
        conn.execute(
            "ALTER TABLE helius_webhook_events ADD COLUMN raw_sample TEXT"
        )
    except Exception:
        pass

    # Última vez que cada wallet vigilada entregó un evento. Sin esto, que una
    # wallet deje de llegar es indistinguible de que el trader no opere.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watched_wallet_activity(
            wallet TEXT PRIMARY KEY,
            trader TEXT,
            last_event_ts REAL,
            events INTEGER DEFAULT 0
        )
        """
    )

    # Sembrar la tabla con el historial ya capturado, para que el monitor
    # arranque sabiendo cuándo se vio cada wallet por última vez en vez de
    # tratar a todas como nuevas.
    conn.execute(
        """
        INSERT INTO watched_wallet_activity(
            wallet,
            trader,
            last_event_ts,
            events
        )
        SELECT
            wallet,
            trader,
            MAX(ts),
            COUNT(*)
        FROM trades
        WHERE source = 'live'
        AND wallet IS NOT NULL
        AND wallet != ''
        GROUP BY wallet
        ON CONFLICT(wallet) DO NOTHING
        """
    )


    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evaluations(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            trade_signature TEXT,

            event_index INTEGER NOT NULL DEFAULT 0,

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

            reasons TEXT,

            market_cap REAL DEFAULT 0,

            sol_amount REAL DEFAULT 0,

            data_version INTEGER DEFAULT 0,

            UNIQUE(trade_signature, event_index)

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

        mode TEXT DEFAULT 'paper',

        -- Momento on-chain del evento que abrió la posición, para poder
        -- descartar operaciones anteriores a la entrada. NULL cuando no se
        -- conoce: PumpPortal no manda timestamp y las posiciones viejas no lo
        -- tienen. `opened_ts` no sirve para esto, porque mide cuándo
        -- reaccionamos nosotros y no cuándo ocurrió el evento.
        entry_block_event_ts REAL,

        -- Último evento on-chain aplicado a esta posición. Evita que un
        -- webhook atrasado haga retroceder su estado después de haber aplicado
        -- otro evento más nuevo. NULL conserva el comportamiento anterior.
        last_applied_block_event_ts REAL

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

    # Qué eventos ya se aplicaron a qué posición.
    #
    # `update_paper_position()` descuenta de `remaining_pct` y acumula en
    # `realized_pnl_usd`: aplicar dos veces el mismo evento convierte una venta
    # parcial del 25% en una del 50% y suma la ganancia dos veces. Un reintento
    # tras un fallo de red basta para provocarlo.
    #
    # La fila se escribe dentro de la misma transacción que modifica la
    # posición, para que no pueda quedar una sin la otra.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_position_applications(
            position_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            applied_ts REAL NOT NULL,
            action TEXT DEFAULT '',
            PRIMARY KEY (position_id, event_id)
        )
        """
    )


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

            source TEXT DEFAULT 'live',

            -- Identidad del evento que originó la fila, `firma:índice`.
            --
            -- Deliberadamente no incluye `source`: la misma operación traída
            -- por PumpPortal y por Helius es una sola y tiene que guardarse una
            -- sola vez. NULL para las rutas de demo, que no tienen identidad
            -- que ofrecer, y para las filas anteriores a esta columna.
            event_id TEXT

        )
        """
    )

    # Único solo donde hay identidad. El índice parcial deja fuera las filas
    # sin `event_id`, que pueden repetirse legítimamente, y además reemplaza el
    # barrido por `signature` que hacía la deduplicación anterior: esa columna
    # no tiene índice, así que cada guardado recorría la tabla entera.
    #
    # En una base que todavía no tiene la columna —`migrate_database()` llama a
    # `db()` antes de agregarla— esto no puede correr. Se saltea en silencio y
    # lo crea la migración; de ahí en adelante es un no-op.
    #
    # La columna NO se agrega acá: `db()` abre conexión constantemente y un
    # `ALTER TABLE` que falla en cada una cuesta una excepción por conexión y
    # más contención. Va una sola vez, en `migrate_database()`.
    try:
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_token_history_event_id
            ON token_history(event_id)
            WHERE event_id IS NOT NULL
            """
        )
    except sqlite3.OperationalError:
        pass

    conn.execute("""
CREATE TABLE IF NOT EXISTS signal_outcomes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    mint TEXT NOT NULL,
    trader TEXT,
    signal_ts REAL NOT NULL,

    price_at_signal REAL DEFAULT 0,

    price_10s REAL,
    price_30s REAL,
    price_1m REAL,
    price_5m REAL,
    price_15m REAL,
                 
    observed_10s_ts REAL,
    observed_30s_ts REAL,
    observed_1m_ts REAL,
    observed_5m_ts REAL,
    observed_15m_ts REAL,

    max_price REAL,
    min_price REAL,

    return_10s REAL,
    return_30s REAL,
    return_1m REAL,
    return_5m REAL,
    return_15m REAL,

    max_return REAL,
    min_return REAL,

    hit_tp25 INTEGER DEFAULT 0,
    hit_tp50 INTEGER DEFAULT 0,
    hit_sl10 INTEGER DEFAULT 0,

    tp25_ts REAL,
    tp50_ts REAL,
    sl10_ts REAL,
                 
    status TEXT DEFAULT 'active',             
                 

    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL
)
""")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_signal_outcomes_trader_mint_ts
        ON signal_outcomes(trader, mint, signal_ts, id)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_shadow_predictions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluation_id INTEGER NOT NULL,
            created_ts REAL NOT NULL,
            model_version TEXT NOT NULL,
            data_version INTEGER NOT NULL,
            probability REAL NOT NULL,
            threshold REAL NOT NULL,
            predicted_target INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            UNIQUE(evaluation_id, model_version)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_shadow_predictions_created_ts
        ON model_shadow_predictions(created_ts)
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
            parent_order_id INTEGER DEFAULT NULL,
            external_signature TEXT DEFAULT NULL
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

    try:
        conn.execute(
            """
            ALTER TABLE execution_orders
            ADD COLUMN external_signature TEXT DEFAULT NULL
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


    order_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(execution_orders)")
    }
    if "trade_wallet" not in order_columns:
        conn.execute("ALTER TABLE execution_orders ADD COLUMN trade_wallet TEXT")
    if "requested_token_amount_raw" not in order_columns:
        conn.execute(
            "ALTER TABLE execution_orders ADD COLUMN requested_token_amount_raw TEXT"
        )
    for column, definition in (
        ("entry_market_cap_sol", "REAL"),
        ("origin_trader", "TEXT"),
        ("exit_reason", "TEXT"),
        ("target_tp_stage", "INTEGER"),
    ):
        if column not in order_columns:
            conn.execute(
                f"ALTER TABLE execution_orders ADD COLUMN {column} {definition}"
            )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_positions(
            order_id INTEGER PRIMARY KEY REFERENCES execution_orders(id),
            signature TEXT NOT NULL UNIQUE,
            wallet TEXT NOT NULL,
            mint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            token_amount_raw TEXT NOT NULL,
            remaining_amount_raw TEXT NOT NULL,
            token_decimals INTEGER NOT NULL,
            net_sol_debit_lamports TEXT NOT NULL,
            network_fee_lamports TEXT NOT NULL,
            cash_cost_per_token_sol TEXT NOT NULL,
            remaining_cost_basis_lamports TEXT,
            entry_market_cap_sol REAL,
            current_market_cap_sol REAL,
            origin_trader TEXT,
            tp_stage INTEGER NOT NULL DEFAULT 0,
            last_exit_reason TEXT,
            entry_block_time REAL,
            last_applied_block_event_ts REAL,
            fill_json TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            recorded_ts REAL NOT NULL
        )
        """
    )
    position_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(live_positions)")
    }
    if "remaining_cost_basis_lamports" not in position_columns:
        conn.execute(
            "ALTER TABLE live_positions ADD COLUMN remaining_cost_basis_lamports TEXT"
        )
    for column, definition in (
        ("entry_market_cap_sol", "REAL"),
        ("current_market_cap_sol", "REAL"),
        ("origin_trader", "TEXT"),
        ("tp_stage", "INTEGER NOT NULL DEFAULT 0"),
        ("last_exit_reason", "TEXT"),
        ("entry_block_time", "REAL"),
        ("last_applied_block_event_ts", "REAL"),
    ):
        if column not in position_columns:
            conn.execute(
                f"ALTER TABLE live_positions ADD COLUMN {column} {definition}"
            )
    conn.execute(
        "UPDATE live_positions SET remaining_cost_basis_lamports = "
        "net_sol_debit_lamports WHERE remaining_cost_basis_lamports IS NULL"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_position_sales(
            sell_order_id INTEGER PRIMARY KEY REFERENCES execution_orders(id),
            position_order_id INTEGER NOT NULL REFERENCES live_positions(order_id),
            signature TEXT NOT NULL UNIQUE,
            token_amount_raw TEXT NOT NULL,
            net_sol_credit_lamports TEXT NOT NULL,
            network_fee_lamports TEXT NOT NULL,
            allocated_cost_basis_lamports TEXT NOT NULL,
            realized_pnl_lamports TEXT NOT NULL,
            block_time REAL,
            fill_json TEXT NOT NULL,
            receipt_json TEXT NOT NULL,
            recorded_ts REAL NOT NULL
        )
        """
    )
    sale_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(live_position_sales)")
    }
    if "block_time" not in sale_columns:
        conn.execute("ALTER TABLE live_position_sales ADD COLUMN block_time REAL")
    conn.commit()

    return conn



def mark_market_event_processed(
    signature,
    event_index=0,
    source="live",
):
    """Reserva una operación por identidad completa antes de sus efectos."""
    event_id = market_event_identity(signature, event_index)

    if event_id is None:
        return False

    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO processed_market_events(
                event_id, signature, event_index, ts, source
            )
            VALUES(?,?,?,?,?)
            """,
            (event_id, signature, event_index, time.time(), source),
        )

        # Mantener la tabla vieja para que una reversión del despliegue no
        # vuelva a aplicar los eventos comunes, que PumpPortal representa como
        # índice 0. El código viejo no puede distinguir índices superiores.
        if cursor.rowcount and event_index == 0:
            conn.execute(
                """
                INSERT OR IGNORE INTO processed_signatures(
                    signature, ts, source
                )
                VALUES(?,?,?)
                """,
                (signature, time.time(), source),
            )

        conn.commit()
        return bool(cursor.rowcount)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def mark_signature_processed(signature, source="live"):
    """Compatibilidad: una firma sin índice representa la operación cero."""
    return mark_market_event_processed(
        signature,
        event_index=0,
        source=source,
    )

def count_open_positions(
    mode="paper",
    connection=None
):

    conn = connection or db()

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

    live_count = 0
    if mode == "live":
        live_count = conn.execute(
            "SELECT COUNT(*) FROM live_positions WHERE status = 'open'"
        ).fetchone()[0]
    if connection is None:
        conn.close()

    return int(row[0] or 0) + live_count


def get_daily_realized_pnl(
    mode="paper",
    connection=None
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

    conn = connection or db()

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

    if connection is None:
        conn.close()

    return float(row[0] or 0)


def get_daily_live_realized_pnl_sol(connection=None):
    now = time.localtime()
    start_of_day = time.mktime((
        now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0,
        now.tm_wday, now.tm_yday, now.tm_isdst,
    ))
    conn = connection or db()
    rows = conn.execute(
        "SELECT realized_pnl_lamports FROM live_position_sales "
        "WHERE COALESCE(block_time, recorded_ts) >= ?",
        (start_of_day,),
    ).fetchall()
    if connection is None:
        conn.close()
    return sum(int(row[0]) for row in rows) / 1_000_000_000


def get_local_day_start_ts(now=None):
    current = time.localtime(now if now is not None else time.time())
    return time.mktime((
        current.tm_year, current.tm_mon, current.tm_mday, 0, 0, 0,
        current.tm_wday, current.tm_yday, current.tm_isdst,
    ))


def get_daily_live_buy_exposure(now=None, connection=None, trader=None):
    start_of_day = get_local_day_start_ts(now)
    conn = connection or db()
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(amount_usd), 0), "
        "(SELECT COUNT(*) FROM execution_orders "
        " WHERE mode = 'live' AND source = 'pumpportal_lightning' "
        " AND status IN ('SENT', 'PENDING_RECONCILIATION') "
        " AND external_signature IS NULL) "
        "FROM execution_orders WHERE mode = 'live' AND side = 'buy' "
        "AND ts_created >= ?",
        (start_of_day,),
    ).fetchone()
    normalized_trader = str(trader or "").strip()
    trader_attempts = 0
    if normalized_trader:
        trader_row = conn.execute(
            "SELECT COUNT(*) FROM execution_orders "
            "WHERE mode = 'live' AND side = 'buy' AND ts_created >= ? "
            "AND origin_trader = ?",
            (start_of_day, normalized_trader),
        ).fetchone()
        trader_attempts = int(trader_row[0] or 0)
    if connection is None:
        conn.close()
    return {
        "attempts": int(row[0] or 0),
        "notional_usd": float(row[1] or 0),
        "unresolved_without_signature": int(row[2] or 0),
        "trader_attempts": trader_attempts,
    }


def get_live_position_summary(limit=100):
    safe_limit = max(1, min(int(limit), 500))
    conn = db()
    positions = conn.execute(
        """SELECT order_id, wallet, mint, status, token_amount_raw,
                  remaining_amount_raw, token_decimals,
                  net_sol_debit_lamports, remaining_cost_basis_lamports,
                  network_fee_lamports, recorded_ts, entry_market_cap_sol,
                  current_market_cap_sol, origin_trader, tp_stage,
                  last_exit_reason, entry_block_time,
                  last_applied_block_event_ts
           FROM live_positions ORDER BY recorded_ts DESC LIMIT ?""",
        (safe_limit,),
    ).fetchall()
    totals = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) "
        "FROM live_positions"
    ).fetchone()
    sales = conn.execute(
        """SELECT sell_order_id, position_order_id, signature, token_amount_raw,
                  net_sol_credit_lamports, network_fee_lamports,
                  allocated_cost_basis_lamports, realized_pnl_lamports,
                  block_time, recorded_ts
           FROM live_position_sales ORDER BY recorded_ts DESC LIMIT ?""",
        (safe_limit,),
    ).fetchall()
    conn.close()
    return {
        "total": int(totals[0] or 0),
        "open": int(totals[1] or 0),
        "positions": [
            {
                "order_id": row[0], "wallet": row[1], "mint": row[2],
                "status": row[3], "token_amount_raw": row[4],
                "remaining_amount_raw": row[5], "token_decimals": row[6],
                "net_sol_debit_lamports": row[7],
                "remaining_cost_basis_lamports": row[8],
                "network_fee_lamports": row[9], "recorded_ts": row[10],
                "entry_market_cap_sol": row[11],
                "current_market_cap_sol": row[12],
                "origin_trader": row[13], "tp_stage": row[14],
                "last_exit_reason": row[15],
                "entry_block_time": row[16],
                "last_applied_block_event_ts": row[17],
            }
            for row in positions
        ],
        "sales": [
            {
                "sell_order_id": row[0], "position_order_id": row[1],
                "signature": row[2], "token_amount_raw": row[3],
                "net_sol_credit_lamports": row[4],
                "network_fee_lamports": row[5],
                "allocated_cost_basis_lamports": row[6],
                "realized_pnl_lamports": row[7], "block_time": row[8],
                "recorded_ts": row[9],
            }
            for row in sales
        ],
        "daily_realized_pnl_sol": get_daily_live_realized_pnl_sol(),
    }


def validate_slippage(
    expected_price,
    execution_price
):

    try:
        expected_price = float(expected_price)
        execution_price = float(execution_price)
    except (TypeError, ValueError):
        return False

    if (
        not math.isfinite(expected_price)
        or not math.isfinite(execution_price)
        or expected_price <= 0
        or execution_price <= 0
    ):
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

    try:
        liquidity_sol = float(liquidity_sol)
    except (TypeError, ValueError):
        return False

    if (
        not math.isfinite(liquidity_sol)
        or liquidity_sol < MIN_LIQUIDITY_SOL
    ):
        print(
            f"[RISK BLOCK] Liquidez {liquidity_sol:.2f} SOL "
            f"menor al mínimo {MIN_LIQUIDITY_SOL:.2f} SOL"
        )
        return False

    return True


def create_signal_outcome(
    signal_id,
    mint,
    trader,
    signal_ts,
    price_at_signal,
):
    now = time.time()

    conn = db()
    cursor = conn.execute(
        """
        INSERT INTO signal_outcomes(
            signal_id,
            mint,
            trader,
            signal_ts,
            price_at_signal,
            max_price,
            min_price,
            created_ts,
            updated_ts
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            signal_id,
            mint,
            trader,
            float(signal_ts),
            float(price_at_signal or 0),
            float(price_at_signal or 0),
            float(price_at_signal or 0),
            now,
            now,
        ),
    )
    conn.commit()
    outcome_id = cursor.lastrowid
    conn.close()

    return outcome_id


def update_signal_outcome_10s(
    outcome_id,
    current_price,
    observed_ts=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT price_at_signal
        FROM signal_outcomes
        WHERE id=?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    price_at_signal = float(row[0] or 0)

    if price_at_signal > 0:
        return_10s = (
            (float(current_price) - price_at_signal)
            / price_at_signal
        ) * 100
    else:
        return_10s = 0.0

    now = float(observed_ts if observed_ts is not None else time.time())

    conn.execute(
        """
        UPDATE signal_outcomes
        SET
        price_10s=?,
        return_10s=?,
        observed_10s_ts=?,
            max_price=CASE
                WHEN max_price IS NULL OR ? > max_price
                THEN ?
                ELSE max_price
            END,
            min_price=CASE
                WHEN min_price IS NULL OR ? < min_price
                THEN ?
                ELSE min_price
            END,
            updated_ts=?
        WHERE id=?
        """,
        (
            float(current_price),
            return_10s,
            now,
            float(current_price),
            float(current_price),
            float(current_price),
            float(current_price),
            now,
            outcome_id,
        )
    )

    conn.commit()
    conn.close()

    return True


def update_signal_outcome_30s(
    outcome_id,
    current_price,
    observed_ts=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT price_at_signal
        FROM signal_outcomes
        WHERE id=?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    price_at_signal = float(row[0] or 0)

    if price_at_signal > 0:
        return_30s = (
            (float(current_price) - price_at_signal)
            / price_at_signal
        ) * 100
    else:
        return_30s = 0.0

    now = float(observed_ts if observed_ts is not None else time.time())

    conn.execute(
        """
        UPDATE signal_outcomes
        SET
            price_30s=?,
            return_30s=?,
            observed_30s_ts=?,
            max_price=CASE
                WHEN max_price IS NULL OR ? > max_price
                THEN ?
                ELSE max_price
            END,
            min_price=CASE
                WHEN min_price IS NULL OR ? < min_price
                THEN ?
                ELSE min_price
            END,
            updated_ts=?
        WHERE id=?
        """,
        (
            float(current_price),
            return_30s,
            now,
            float(current_price),
            float(current_price),
            float(current_price),
            float(current_price),
            now,
            outcome_id,
        )
    )

    conn.commit()
    conn.close()

    return True

def update_signal_outcome_1m(
    outcome_id,
    current_price,
    observed_ts=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT price_at_signal
        FROM signal_outcomes
        WHERE id=?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    price_at_signal = float(row[0] or 0)

    if price_at_signal > 0:
        return_1m = (
            (float(current_price) - price_at_signal)
            / price_at_signal
        ) * 100
    else:
        return_1m = 0.0

    now = float(observed_ts if observed_ts is not None else time.time())

    conn.execute(
        """
        UPDATE signal_outcomes
        SET
            price_1m=?,
            return_1m=?,
            observed_1m_ts=?,
            max_price=CASE
                WHEN max_price IS NULL OR ? > max_price
                THEN ?
                ELSE max_price
            END,
            min_price=CASE
                WHEN min_price IS NULL OR ? < min_price
                THEN ?
                ELSE min_price
            END,
            updated_ts=?
        WHERE id=?
        """,
        (
            float(current_price),
            return_1m,
            now,
            float(current_price),
            float(current_price),
            float(current_price),
            float(current_price),
            now,
            outcome_id,
        )
    )

    conn.commit()
    conn.close()

    return True

def update_signal_outcome_5m(
    outcome_id,
    current_price,
    observed_ts=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT price_at_signal
        FROM signal_outcomes
        WHERE id=?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    price_at_signal = float(row[0] or 0)

    if price_at_signal > 0:
        return_5m = (
            (float(current_price) - price_at_signal)
            / price_at_signal
        ) * 100
    else:
        return_5m = 0.0

    now = float(observed_ts if observed_ts is not None else time.time())

    conn.execute(
        """
        UPDATE signal_outcomes
        SET
            price_5m=?,
            return_5m=?,
            observed_5m_ts=?,
            max_price=CASE
                WHEN max_price IS NULL OR ? > max_price
                THEN ?
                ELSE max_price
            END,
            min_price=CASE
                WHEN min_price IS NULL OR ? < min_price
                THEN ?
                ELSE min_price
            END,
            updated_ts=?
        WHERE id=?
        """,
        (
            float(current_price),
            return_5m,
            now,
            float(current_price),
            float(current_price),
            float(current_price),
            float(current_price),
            now,
            outcome_id,
        )
    )

    conn.commit()
    conn.close()

    return True

def update_signal_outcome_15m(
    outcome_id,
    current_price,
    observed_ts=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT price_at_signal
        FROM signal_outcomes
        WHERE id=?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    price_at_signal = float(row[0] or 0)

    if price_at_signal > 0:
        return_15m = (
            (float(current_price) - price_at_signal)
            / price_at_signal
        ) * 100
    else:
        return_15m = 0.0

    now = float(observed_ts if observed_ts is not None else time.time())

    conn.execute(
        """
        UPDATE signal_outcomes
        SET
            price_15m=?,
            return_15m=?,
            observed_15m_ts=?,
            max_price=CASE
                WHEN max_price IS NULL OR ? > max_price
                THEN ?
                ELSE max_price
            END,
            min_price=CASE
                WHEN min_price IS NULL OR ? < min_price
                THEN ?
                ELSE min_price
            END,
            updated_ts=?
        WHERE id=?
        """,
        (
            float(current_price),
            return_15m,
            now,
            float(current_price),
            float(current_price),
            float(current_price),
            float(current_price),
            now,
            outcome_id,
        )
    )

    conn.commit()
    conn.close()

    return True

def update_signal_outcome_extremes(
    outcome_id,
    current_price,
    observed_ts=None,
):
    conn = db()

    row = conn.execute(
        """
        SELECT
            price_at_signal,
            max_price,
            min_price
        FROM signal_outcomes
        WHERE id = ?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    price_at_signal = float(row[0] or 0)
    old_max_price = float(row[1] or 0)
    old_min_price = float(row[2] or 0)

    if price_at_signal <= 0:
        conn.close()
        return False

    current_price = float(current_price)

    if old_max_price <= 0:
        new_max_price = current_price
    else:
        new_max_price = max(
            old_max_price,
            current_price
        )

    if old_min_price <= 0:
        new_min_price = current_price
    else:
        new_min_price = min(
            old_min_price,
            current_price
        )

    max_return = (
        (new_max_price - price_at_signal)
        / price_at_signal
    ) * 100

    min_return = (
        (new_min_price - price_at_signal)
        / price_at_signal
    ) * 100

    hit_tp25 = 1 if max_return >= 25 else 0
    hit_tp50 = 1 if max_return >= 50 else 0
    hit_sl10 = 1 if min_return <= -10 else 0

    now = float(observed_ts if observed_ts is not None else time.time())

    conn.execute(
        """
                UPDATE signal_outcomes
        SET
            max_price = ?,
            min_price = ?,
            max_return = ?,
            min_return = ?,

            tp25_ts = CASE
                WHEN tp25_ts IS NULL AND ? = 1
                THEN ?
                ELSE tp25_ts
            END,

            tp50_ts = CASE
                WHEN tp50_ts IS NULL AND ? = 1
                THEN ?
                ELSE tp50_ts
            END,

            sl10_ts = CASE
                WHEN sl10_ts IS NULL AND ? = 1
                THEN ?
                ELSE sl10_ts
            END,

            hit_tp25 = MAX(hit_tp25, ?),
            hit_tp50 = MAX(hit_tp50, ?),
            hit_sl10 = MAX(hit_sl10, ?),

            updated_ts = ?
        WHERE id = ?
        """,
                (
            new_max_price,
            new_min_price,
            max_return,
            min_return,

            hit_tp25,
            now,

            hit_tp50,
            now,

            hit_sl10,
            now,

            hit_tp25,
            hit_tp50,
            hit_sl10,

            now,
            outcome_id,
        )
    )

    conn.commit()
    conn.close()

    return True


def process_signal_outcomes_event(
    mint,
    event,
):
    if not mint:
        return

    v_sol = float(
        event.get("vSolInBondingCurve")
        or 0
    )

    v_tokens = float(
        event.get("vTokensInBondingCurve")
        or 0
    )

    if v_tokens > 0:
        current_price = v_sol / v_tokens
    else:
        return

    now = market_event_block_ts(event) or time.time()

    previous_price = LAST_TOKEN_PRICE.get(mint)
    previous_ts = None
    if isinstance(previous_price, dict):
        previous_ts = stored_block_event_ts(previous_price.get("ts"))

    if previous_ts is None or now >= previous_ts:
        LAST_TOKEN_PRICE[mint] = {
            "price": float(current_price),
            "ts": now,
        }

    conn = db()

    rows = conn.execute(
        """
        SELECT
    id,
    signal_ts,
    price_10s,
    price_30s,
    price_1m,
    price_5m,
    price_15m,
    observed_10s_ts,
    observed_30s_ts,
    observed_1m_ts,
    observed_5m_ts,
    observed_15m_ts
FROM signal_outcomes
WHERE mint = ?
AND (
    price_10s IS NULL
    OR price_30s IS NULL
    OR price_1m IS NULL
    OR price_5m IS NULL
    OR price_15m IS NULL
)
        """,
        (mint,)
    ).fetchall()

    conn.close()

    for row in rows:
        outcome_id = int(row[0])
        signal_ts = float(row[1] or 0)
        price_10s = row[2]
        price_30s = row[3]
        price_1m = row[4]
        price_5m = row[5]
        price_15m = row[6]
        observed_10s_ts = row[7]
        observed_30s_ts = row[8]
        observed_1m_ts = row[9]
        observed_5m_ts = row[10]
        observed_15m_ts = row[11]
        elapsed = now - signal_ts

        if elapsed < 0:
            continue

        update_signal_outcome_extremes(
            outcome_id=outcome_id,
            current_price=current_price,
            observed_ts=now,
        )

        if elapsed >= 10 and (
            price_10s is None
            or observed_10s_ts is None
            or now < float(observed_10s_ts)
        ):
            update_signal_outcome_10s(
                outcome_id=outcome_id,
                current_price=current_price,
                observed_ts=now,
            )

        if elapsed >= 30 and (
            price_30s is None
            or observed_30s_ts is None
            or now < float(observed_30s_ts)
        ):
            update_signal_outcome_30s(
                outcome_id=outcome_id,
                current_price=current_price,
                observed_ts=now,
            )

        if elapsed >= 60 and (
            price_1m is None
            or observed_1m_ts is None
            or now < float(observed_1m_ts)
        ):
            update_signal_outcome_1m(
                outcome_id=outcome_id,
                current_price=current_price,
                observed_ts=now,
            )

        if elapsed >= 300 and (
            price_5m is None
            or observed_5m_ts is None
            or now < float(observed_5m_ts)
        ):
            update_signal_outcome_5m(
                outcome_id=outcome_id,
                current_price=current_price,
                observed_ts=now,
            )

        if elapsed >= 900 and (
            price_15m is None
            or observed_15m_ts is None
            or now < float(observed_15m_ts)
        ):
            update_signal_outcome_15m(
                outcome_id=outcome_id,
                current_price=current_price,
                observed_ts=now,
            )

        cleanup_finished_outcome_token(mint)

async def signal_outcome_checkpoint_worker():
    while True:
        try:
            now = time.time()

            if not STREAM_CONNECTED:
                await asyncio.sleep(2)
                continue

            conn = db()

            rows = conn.execute(
                """
                SELECT
                    id,
                    mint,
                    signal_ts,
                    price_10s,
                    price_30s,
                    price_1m,
                    price_5m,
                    price_15m

                FROM signal_outcomes

                WHERE status = 'active'
                AND signal_ts >= ?
                AND (
                    price_10s IS NULL
                    OR price_30s IS NULL
                    OR price_1m IS NULL
                    OR price_5m IS NULL
                    OR price_15m IS NULL
                )
                """,
                (now - 1200,)
            ).fetchall()

            conn.close()

            touched_mints = set()

            for row in rows:
                outcome_id = int(row[0])
                mint = str(row[1] or "")
                signal_ts = float(row[2] or 0)

                price_10s = row[3]
                price_30s = row[4]
                price_1m = row[5]
                price_5m = row[6]
                price_15m = row[7]

                if not mint or signal_ts <= 0:
                    continue

                cached = LAST_TOKEN_PRICE.get(mint)

                if not cached:
                    continue

                current_price = float(
                    cached.get("price") or 0
                )

                price_ts = float(
                    cached.get("ts") or 0
                )

                if current_price <= 0:
                    continue

                # Nunca usar un precio observado antes de la señal.
                if price_ts < signal_ts:
                    continue

                elapsed = now - signal_ts

                # Solo rellenamos cerca del checkpoint esperado.
                # Así evitamos reconstruir datos históricos tarde
                # con un precio que no corresponde a ese momento.

                if (
                    price_10s is None
                    and 10 <= elapsed <= 15
                ):
                    update_signal_outcome_10s(
                        outcome_id=outcome_id,
                        current_price=current_price,
                    )

                if (
                    price_30s is None
                    and 30 <= elapsed <= 40
                ):
                    update_signal_outcome_30s(
                        outcome_id=outcome_id,
                        current_price=current_price,
                    )

                if (
                    price_1m is None
                    and 60 <= elapsed <= 75
                ):
                    update_signal_outcome_1m(
                        outcome_id=outcome_id,
                        current_price=current_price,
                    )

                if (
                    price_5m is None
                    and 300 <= elapsed <= 330
                ):
                    update_signal_outcome_5m(
                        outcome_id=outcome_id,
                        current_price=current_price,
                    )

                if (
                    price_15m is None
                    and 900 <= elapsed <= 960
                ):
                    update_signal_outcome_15m(
                        outcome_id=outcome_id,
                        current_price=current_price,
                    )

                touched_mints.add(mint)

            for mint in touched_mints:
                cleanup_finished_outcome_token(mint)

            if touched_mints:
                await maybe_send_shadow_review_alert()

        except Exception as exc:
            print(
                "[CHECKPOINT WORKER ERROR]",
                repr(exc)
            )

        await asyncio.sleep(2)        


def complete_finished_signal_outcomes(mint):
    if not mint:
        return 0

    conn = db()

    cursor = conn.execute(
        """
        UPDATE signal_outcomes
        SET
            status = 'completed',
            updated_ts = ?
        WHERE mint = ?
        AND status IN ('active', 'expired')
        AND price_10s IS NOT NULL
        AND price_30s IS NOT NULL
        AND price_1m IS NOT NULL
        AND price_5m IS NOT NULL
        AND price_15m IS NOT NULL
        """,
        (
            time.time(),
            mint,
        )
    )

    conn.commit()
    affected = cursor.rowcount
    conn.close()

    return affected


def reconcile_finished_signal_outcomes():
    conn = db()

    cursor = conn.execute(
        """
        UPDATE signal_outcomes
        SET
            status = 'completed',
            updated_ts = ?
        WHERE status IN ('active', 'expired')
        AND price_10s IS NOT NULL
        AND price_30s IS NOT NULL
        AND price_1m IS NOT NULL
        AND price_5m IS NOT NULL
        AND price_15m IS NOT NULL
        """,
        (time.time(),)
    )

    conn.commit()
    affected = cursor.rowcount
    conn.close()

    return affected


def expire_old_signal_outcomes(mint):
    if not mint:
        return 0

    cutoff = time.time() - 1200

    conn = db()

    cursor = conn.execute(
        """
        UPDATE signal_outcomes
        SET
            status = 'expired',
            updated_ts = ?
        WHERE mint = ?
        AND status = 'active'
        AND signal_ts < ?
        AND (
            price_10s IS NULL
            OR price_30s IS NULL
            OR price_1m IS NULL
            OR price_5m IS NULL
            OR price_15m IS NULL
        )
        """,
        (
            time.time(),
            mint,
            cutoff,
        )
    )

    conn.commit()
    affected = cursor.rowcount
    conn.close()

    return affected 
            



def cleanup_finished_outcome_token(mint):
    if not mint:
        return

    complete_finished_signal_outcomes(mint)
    expire_old_signal_outcomes(mint)

    conn = db()

    active_cutoff = time.time() - 1200

    unfinished = conn.execute(
    """
    SELECT COUNT(*)
    FROM signal_outcomes
    WHERE mint = ?
    AND signal_ts >= ?
    AND status = 'active'
    AND (
        price_10s IS NULL
        OR price_30s IS NULL
        OR price_1m IS NULL
        OR price_5m IS NULL
        OR price_15m IS NULL
    )
    """,
    (
        mint,
        active_cutoff,
    )
).fetchone()[0]

    open_paper = conn.execute(
        """
        SELECT COUNT(*)
        FROM paper_positions
        WHERE mint = ?
        AND status = 'open'
        """,
        (mint,)
    ).fetchone()[0]

    conn.close()

    if unfinished == 0 and open_paper == 0:
        TRACKED_TOKENS.discard(mint)
        TOKENS_TO_UNSUBSCRIBE.add(mint)

        print(
            f"[TRACKER] Token finalizado: {mint}"
        )   

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


def execute_order(
    mint,
    expected_price,
    execution_price,
    liquidity_sol,
    amount_usd,
    force_fail=False,
    idempotency_key=None,
    parent_order_id=None,
    mode="paper",
    provider=None
):
    selected_provider = str(
        provider or EXECUTION_PROVIDER
    ).strip().lower()

    if mode == "live":
        return {
            "ok": False,
            "reason": "LIVE_EXECUTION_NOT_IMPLEMENTED",
            "provider": selected_provider
        }

    if selected_provider != "simulation":
        return {
            "ok": False,
            "reason": "EXECUTION_PROVIDER_NOT_AVAILABLE",
            "provider": selected_provider
        }

    result = simulate_execution(
        mint=mint,
        expected_price=expected_price,
        execution_price=execution_price,
        liquidity_sol=liquidity_sol,
        amount_usd=amount_usd,
        force_fail=force_fail,
        idempotency_key=idempotency_key,
        parent_order_id=parent_order_id,
        mode=mode
    )
    result["provider"] = selected_provider
    return result


def build_pumpportal_lightning_buy_payload(
    mint,
    amount_usd,
    sol_usd_price,
    quote_ts,
    slippage_pct=MAX_SLIPPAGE_PCT,
    priority_fee_sol=0.00005,
    pool="auto",
    now_ts=None
):
    mint = str(mint or "").strip()
    base58 = set(
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        "abcdefghijkmnopqrstuvwxyz"
    )
    if not 32 <= len(mint) <= 44 or any(
        character not in base58
        for character in mint
    ):
        raise ValueError("INVALID_SOLANA_MINT")

    try:
        amount_usd = float(amount_usd)
        sol_usd_price = float(sol_usd_price)
        quote_ts = float(quote_ts)
        slippage_pct = float(slippage_pct)
        priority_fee_sol = float(priority_fee_sol)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_TRADE_NUMBER") from exc

    numeric_values = (
        amount_usd,
        sol_usd_price,
        quote_ts,
        slippage_pct,
        priority_fee_sol,
    )
    if not all(math.isfinite(value) for value in numeric_values):
        raise ValueError("INVALID_TRADE_NUMBER")
    if amount_usd <= 0 or amount_usd > MAX_POSITION_USD:
        raise ValueError("INVALID_AMOUNT_USD")
    if sol_usd_price <= 0:
        raise ValueError("INVALID_SOL_USD_PRICE")

    quote_age = float(now_ts or time.time()) - quote_ts
    if quote_age < 0 or quote_age > SOL_PRICE_MAX_AGE_SECONDS:
        raise ValueError("STALE_SOL_USD_PRICE")
    if slippage_pct <= 0 or slippage_pct > MAX_SLIPPAGE_PCT:
        raise ValueError("INVALID_SLIPPAGE")
    if not 0 <= priority_fee_sol <= MAX_PRIORITY_FEE_SOL:
        raise ValueError("INVALID_PRIORITY_FEE")

    pool = str(pool or "").strip().lower()
    if pool not in PUMPPORTAL_ALLOWED_POOLS:
        raise ValueError("INVALID_PUMPPORTAL_POOL")

    return {
        "action": "buy",
        "mint": mint,
        "amount": round(amount_usd / sol_usd_price, 9),
        "denominatedInSol": "true",
        "slippage": slippage_pct,
        "priorityFee": priority_fee_sol,
        "pool": pool,
        "skipPreflight": "false",
    }


def build_pumpportal_lightning_sell_payload(
    mint,
    wallet_percentage,
    slippage_pct=MAX_SLIPPAGE_PCT,
    priority_fee_sol=0.00005,
    pool="auto",
):
    mint = str(mint or "").strip()
    base58 = set(
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        "abcdefghijkmnopqrstuvwxyz"
    )
    if not 32 <= len(mint) <= 44 or any(
        character not in base58
        for character in mint
    ):
        raise ValueError("INVALID_SOLANA_MINT")

    try:
        wallet_percentage = float(wallet_percentage)
        slippage_pct = float(slippage_pct)
        priority_fee_sol = float(priority_fee_sol)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_TRADE_NUMBER") from exc

    if not all(math.isfinite(value) for value in (
        wallet_percentage,
        slippage_pct,
        priority_fee_sol,
    )):
        raise ValueError("INVALID_TRADE_NUMBER")
    if not 0 < wallet_percentage <= 100:
        raise ValueError("INVALID_SELL_PERCENTAGE")
    if slippage_pct <= 0 or slippage_pct > MAX_SLIPPAGE_PCT:
        raise ValueError("INVALID_SLIPPAGE")
    if not 0 <= priority_fee_sol <= MAX_PRIORITY_FEE_SOL:
        raise ValueError("INVALID_PRIORITY_FEE")

    pool = str(pool or "").strip().lower()
    if pool not in PUMPPORTAL_ALLOWED_POOLS:
        raise ValueError("INVALID_PUMPPORTAL_POOL")

    percentage_text = f"{wallet_percentage:.9f}".rstrip("0").rstrip(".")
    return {
        "action": "sell",
        "mint": mint,
        "amount": f"{percentage_text}%",
        "denominatedInSol": "false",
        "slippage": slippage_pct,
        "priorityFee": priority_fee_sol,
        "pool": pool,
        "skipPreflight": "false",
    }


def build_pumpportal_exact_sell_payload(
    mint,
    token_amount_raw,
    token_decimals,
    slippage_pct=MAX_SLIPPAGE_PCT,
    priority_fee_sol=0.00005,
    pool="auto",
):
    raw_text = str(token_amount_raw or "").strip()
    if not raw_text.isascii() or not raw_text.isdigit():
        raise ValueError("INVALID_RAW_TOKEN_AMOUNT")
    raw_amount = int(raw_text)
    if raw_amount <= 0 or raw_amount > 2**64 - 1:
        raise ValueError("INVALID_RAW_TOKEN_AMOUNT")
    if type(token_decimals) is not int or not 0 <= token_decimals <= 255:
        raise ValueError("INVALID_TOKEN_DECIMALS")
    with decimal.localcontext() as context:
        context.prec = 100
        token_amount = decimal.Decimal(raw_amount).scaleb(-token_decimals)
    payload = build_pumpportal_lightning_sell_payload(
        mint=mint,
        wallet_percentage=100,
        slippage_pct=slippage_pct,
        priority_fee_sol=priority_fee_sol,
        pool=pool,
    )
    payload["amount"] = format(token_amount, "f")
    return payload


def calculate_wallet_sell_percentage(sell_fraction, remaining_fraction):
    try:
        sell_fraction = float(sell_fraction)
        remaining_fraction = float(remaining_fraction)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_POSITION_FRACTION") from exc

    if not all(math.isfinite(value) for value in (
        sell_fraction,
        remaining_fraction,
    )):
        raise ValueError("INVALID_POSITION_FRACTION")
    if not 0 < remaining_fraction <= 1:
        raise ValueError("INVALID_REMAINING_FRACTION")
    if sell_fraction <= 0 or sell_fraction > remaining_fraction + 1e-9:
        raise ValueError("INVALID_SELL_FRACTION")

    return min(
        100.0,
        min(sell_fraction, remaining_fraction) / remaining_fraction * 100.0,
    )


def prepare_pumpportal_lightning_sell(
    mint,
    sell_fraction,
    remaining_fraction,
    slippage_pct=MAX_SLIPPAGE_PCT,
    priority_fee_sol=0.00005,
    pool="auto",
):
    wallet_percentage = calculate_wallet_sell_percentage(
        sell_fraction,
        remaining_fraction,
    )
    payload = build_pumpportal_lightning_sell_payload(
        mint=mint,
        wallet_percentage=wallet_percentage,
        slippage_pct=slippage_pct,
        priority_fee_sol=priority_fee_sol,
        pool=pool,
    )
    return {
        "provider": "pumpportal_lightning",
        "wallet_percentage": wallet_percentage,
        "payload": payload,
        "submitted": False,
    }


def normalize_solana_signature(signature):
    signature = str(signature or "").strip()
    base58 = set(
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        "abcdefghijkmnopqrstuvwxyz"
    )
    if not 64 <= len(signature) <= 100 or any(
        character not in base58
        for character in signature
    ):
        raise ValueError("INVALID_SOLANA_SIGNATURE")
    return signature


def fetch_sol_usd_quote(timeout_seconds=5):
    request = Request(
        SOL_USD_PRICE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "Pump-Copilot/1.0",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    try:
        price = float(payload["data"]["amount"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("INVALID_SOL_USD_RESPONSE") from exc

    if not math.isfinite(price) or price <= 0:
        raise ValueError("INVALID_SOL_USD_PRICE")

    return {
        "price": price,
        "quoted_ts": time.time(),
        "source": "coinbase_spot",
    }


def prepare_pumpportal_lightning_buy(
    mint,
    amount_usd,
    slippage_pct=MAX_SLIPPAGE_PCT,
    priority_fee_sol=0.00005,
    pool="auto"
):
    quote = fetch_sol_usd_quote()
    payload = build_pumpportal_lightning_buy_payload(
        mint=mint,
        amount_usd=amount_usd,
        sol_usd_price=quote["price"],
        quote_ts=quote["quoted_ts"],
        slippage_pct=slippage_pct,
        priority_fee_sol=priority_fee_sol,
        pool=pool,
    )
    return {
        "provider": "pumpportal_lightning",
        "quote": quote,
        "payload": payload,
        "submitted": False,
    }


def submit_pumpportal_lightning_trade(
    payload,
    api_key=None,
    timeout_seconds=5,
    execution_side=None
):
    side = str(
        execution_side
        or payload.get("action")
        or ""
    ).strip().lower()

    if side not in {"buy", "sell"}:
        return {
            "ok": False,
            "reason": "INVALID_EXECUTION_SIDE",
        }

    try:
        require_live_trading(side)
    except HTTPException as exc:
        return {
            "ok": False,
            "reason": exc.detail,
        }

    selected_api_key = str(api_key or API_KEY or "").strip()
    if not selected_api_key:
        return {
            "ok": False,
            "reason": "PUMPPORTAL_API_KEY_MISSING",
        }

    request = Request(
        "https://pumpportal.fun/api/trade?api-key="
        + selected_api_key,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Pump-Copilot/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {
            "ok": False,
            "reason": "PUMPPORTAL_REQUEST_FAILED",
        }

    if not isinstance(result, dict):
        return {
            "ok": False,
            "reason": "INVALID_PUMPPORTAL_RESPONSE",
        }

    signature = result.get("signature")
    errors = result.get("errors") or result.get("error")
    if signature:
        try:
            signature = normalize_solana_signature(signature)
        except ValueError:
            return {
                "ok": False,
                "reason": "INVALID_PUMPPORTAL_RESPONSE",
            }
    return {
        "ok": bool(signature) and not errors,
        "reason": (
            "PUMPPORTAL_SUBMITTED"
            if signature
            else "PUMPPORTAL_REJECTED"
            if errors
            else "PUMPPORTAL_RESPONSE_WITHOUT_SIGNATURE"
        ),
        "signature": signature,
        "errors": errors,
    }


def execute_pumpportal_lightning_buy(
    mint,
    expected_price,
    execution_price,
    liquidity_sol,
    amount_usd,
    idempotency_key,
    market_cap_sol=None,
    origin_trader="",
):
    try:
        require_live_trading("buy")
    except HTTPException as exc:
        return {"ok": False, "reason": exc.detail}

    readiness = get_live_execution_readiness(
        "buy",
        trader=origin_trader,
        amount_usd=amount_usd,
    )
    if not readiness["ready"]:
        return {
            "ok": False,
            "reason": "LIVE_EXECUTION_NOT_READY",
            "blockers": readiness["blockers"],
        }

    if not str(idempotency_key or "").strip():
        return {"ok": False, "reason": "IDEMPOTENCY_KEY_REQUIRED"}

    if market_cap_sol is None:
        return {"ok": False, "reason": "ENTRY_MARKET_CAP_REQUIRED"}
    try:
        entry_market_cap_sol = float(market_cap_sol)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "INVALID_ENTRY_MARKET_CAP"}
    if not math.isfinite(entry_market_cap_sol) or entry_market_cap_sol <= 0:
        return {"ok": False, "reason": "INVALID_ENTRY_MARKET_CAP"}
    origin_trader = str(origin_trader or "").strip()
    if not origin_trader:
        return {"ok": False, "reason": "ORIGIN_TRADER_REQUIRED"}

    order = create_execution_order_idempotent(
        mint=mint,
        side="buy",
        amount_usd=amount_usd,
        expected_price=expected_price,
        execution_price=execution_price,
        liquidity_sol=liquidity_sol,
        idempotency_key=idempotency_key,
        source="pumpportal_lightning",
        mode="live",
        canary_limits={
            "start_of_day_ts": get_local_day_start_ts(),
            "max_buys": LIVE_CANARY_MAX_BUYS_PER_DAY,
            "max_notional_usd": LIVE_CANARY_MAX_DAILY_NOTIONAL_USD,
            "trader": origin_trader,
        },
    )
    if order.get("blocked"):
        return {
            "ok": False,
            "reason": order["reason"],
        }
    order_id = order["order_id"]
    if not order["created"]:
        current = get_execution_order_status(order_id)
        return {
            "ok": current["status"] == "CONFIRMED",
            "order_id": order_id,
            "status": current["status"],
            "reason": "IDEMPOTENT_REUSE",
        }

    # Bind accounting and entry context before any network submission.
    conn = db()
    conn.execute(
        "UPDATE execution_orders SET trade_wallet = ?, entry_market_cap_sol = ?, "
        "origin_trader = ? WHERE id = ?",
        (PUMPPORTAL_TRADING_WALLET_ADDRESS, entry_market_cap_sol,
         origin_trader, order_id),
    )
    conn.commit()
    conn.close()
    risk = risk_check(
        mint=mint,
        amount_usd=amount_usd,
        expected_price=expected_price,
        execution_price=execution_price,
        liquidity_sol=liquidity_sol,
        mode="live",
        execution_order_id=order_id,
    )
    if not risk["ok"]:
        update_execution_order(order_id, "RISK_BLOCKED", risk["reason"])
        return {"ok": False, "order_id": order_id, "reason": risk["reason"]}

    try:
        prepared = prepare_pumpportal_lightning_buy(mint, amount_usd)
    except Exception:
        update_execution_order(
            order_id,
            "FAILED",
            "PUMPPORTAL_PREPARATION_FAILED",
        )
        return {
            "ok": False,
            "order_id": order_id,
            "reason": "PUMPPORTAL_PREPARATION_FAILED",
        }

    update_execution_order(order_id, "RISK_CHECKED", "RISK_OK")
    update_execution_order(order_id, "SENT", "")
    submitted = submit_pumpportal_lightning_trade(
        prepared["payload"],
        execution_side="buy",
    )
    signature = submitted.get("signature")

    if signature:
        set_execution_order_external_signature(order_id, signature)
        update_execution_order(
            order_id,
            "PENDING_RECONCILIATION",
            "PUMPPORTAL_SUBMITTED",
        )
        return {
            "ok": True,
            "order_id": order_id,
            "status": "PENDING_RECONCILIATION",
            "reason": "PUMPPORTAL_SUBMITTED",
            "signature": signature,
        }

    reason = submitted.get("reason") or "PUMPPORTAL_REQUEST_FAILED"
    status = (
        "FAILED"
        if reason == "PUMPPORTAL_REJECTED"
        else "PENDING_RECONCILIATION"
    )
    update_execution_order(order_id, status, reason)
    return {
        "ok": False,
        "order_id": order_id,
        "status": status,
        "reason": reason,
    }


def execute_pumpportal_lightning_sell(
    position_order_id,
    token_amount_raw,
    idempotency_key,
    slippage_pct=MAX_SLIPPAGE_PCT,
    priority_fee_sol=0.00005,
    pool="auto",
    exit_reason="",
    target_tp_stage=None,
    event_block_event_ts=None,
):
    idempotency_key = str(idempotency_key or "").strip()
    if not idempotency_key:
        return {"ok": False, "reason": "IDEMPOTENCY_KEY_REQUIRED"}
    try:
        require_live_trading("sell")
    except HTTPException as exc:
        return {"ok": False, "reason": exc.detail}
    readiness = get_live_execution_readiness("sell")
    if not readiness["ready"]:
        return {"ok": False, "reason": "LIVE_EXECUTION_NOT_READY",
                "blockers": readiness["blockers"]}

    raw_text = str(token_amount_raw or "").strip()
    if not raw_text.isascii() or not raw_text.isdigit():
        return {"ok": False, "reason": "INVALID_RAW_TOKEN_AMOUNT"}
    requested = int(raw_text)
    if requested <= 0 or requested > 2**64 - 1:
        return {"ok": False, "reason": "INVALID_RAW_TOKEN_AMOUNT"}
    raw_text = str(requested)
    exit_reason = str(exit_reason or "").strip()
    if target_tp_stage is not None:
        target_tp_stage = int(target_tp_stage)
        if target_tp_stage not in (1, 2, 3):
            return {"ok": False, "reason": "INVALID_TARGET_TP_STAGE"}
    event_ts = validated_block_event_ts(
        event_block_event_ts,
        origen="live_sell_event.blockEventTs",
    )
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT order_id FROM execution_idempotency WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            conn.commit()
            current = get_execution_order_status(existing[0])
            return {"ok": current["status"] == "CONFIRMED",
                    "order_id": existing[0], "status": current["status"],
                    "reason": "IDEMPOTENT_REUSE"}
        position = conn.execute(
            "SELECT wallet, mint, status, remaining_amount_raw, token_decimals, "
            "last_applied_block_event_ts "
            "FROM live_positions WHERE order_id = ?", (position_order_id,),
        ).fetchone()
        if not position or position[2] != "open":
            return {"ok": False, "reason": "LIVE_POSITION_NOT_OPEN"}
        if position[0] != PUMPPORTAL_TRADING_WALLET_ADDRESS:
            return {"ok": False, "reason": "LIVE_POSITION_WALLET_MISMATCH"}
        try:
            latest_position_ts = validated_block_event_ts(
                position[5],
                origen="live_positions.last_applied_block_event_ts",
            )
        except ValueError:
            return {
                "ok": False,
                "reason": "INVALID_LIVE_POSITION_TIMESTAMP",
            }
        if (
            event_ts is not None
            and latest_position_ts is not None
            and event_ts < latest_position_ts
        ):
            return {"ok": False, "reason": "STALE_LIVE_EXIT_EVENT"}
        payload = build_pumpportal_exact_sell_payload(
            position[1], raw_text, position[4], slippage_pct, priority_fee_sol, pool,
        )
        pending_rows = conn.execute(
            "SELECT requested_token_amount_raw FROM execution_orders "
            "WHERE parent_order_id = ? AND side = 'sell' "
            "AND status IN ('CREATED','RISK_CHECKED','SENT','PENDING_RECONCILIATION')",
            (position_order_id,),
        ).fetchall()
        if any(row[0] is None for row in pending_rows):
            return {"ok": False, "reason": "UNACCOUNTED_PENDING_SELL"}
        reserved = sum(int(row[0]) for row in pending_rows)
        if requested > int(position[3]) - reserved:
            return {"ok": False, "reason": "SELL_AMOUNT_EXCEEDS_AVAILABLE_POSITION"}
        now = time.time()
        cursor = conn.execute(
            """INSERT INTO execution_orders(
                ts_created, ts_updated, mint, side, amount_usd, expected_price,
                execution_price, liquidity_sol, status, reason, source,
                parent_order_id, mode, trade_wallet, requested_token_amount_raw,
                exit_reason, target_tp_stage
            ) VALUES (?, ?, ?, 'sell', 0, 0, 0, 0, 'CREATED', '',
                      'pumpportal_lightning', ?, 'live', ?, ?, ?, ?)""",
            (now, now, position[1], position_order_id, position[0], raw_text,
             exit_reason, target_tp_stage),
        )
        order_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO execution_idempotency(idempotency_key, order_id, ts) "
            "VALUES (?, ?, ?)", (idempotency_key, order_id, now),
        )
        conn.execute(
            "INSERT INTO execution_order_events(order_id, ts, status, reason) "
            "VALUES (?, ?, 'CREATED', '')", (order_id, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    update_execution_order(order_id, "RISK_CHECKED", "POSITION_AMOUNT_RESERVED")
    update_execution_order(order_id, "SENT", "")
    submitted = submit_pumpportal_lightning_trade(
        payload,
        execution_side="sell",
    )
    signature = submitted.get("signature")
    if signature:
        set_execution_order_external_signature(order_id, signature)
        update_execution_order(order_id, "PENDING_RECONCILIATION", "PUMPPORTAL_SUBMITTED")
        return {"ok": True, "order_id": order_id,
                "status": "PENDING_RECONCILIATION", "signature": signature,
                "reason": "PUMPPORTAL_SUBMITTED"}
    reason = submitted.get("reason") or "PUMPPORTAL_REQUEST_FAILED"
    status = "FAILED" if reason == "PUMPPORTAL_REJECTED" else "PENDING_RECONCILIATION"
    update_execution_order(order_id, status, reason)
    return {"ok": False, "order_id": order_id, "status": status, "reason": reason}


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
    mode="paper",
    canary_limits=None,
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

        if canary_limits is not None:
            canary_trader = str(canary_limits.get("trader") or "").strip()
            if not canary_trader:
                conn.commit()
                conn.close()
                return {
                    "created": False,
                    "blocked": True,
                    "reason": "LIVE_CANARY_TRADER_REQUIRED",
                }
            ambiguous = conn.execute(
                "SELECT COUNT(*) FROM execution_orders "
                "WHERE mode = 'live' AND source = 'pumpportal_lightning' "
                "AND status IN ('SENT', 'PENDING_RECONCILIATION') "
                "AND external_signature IS NULL"
            ).fetchone()
            if int(ambiguous[0] or 0):
                conn.commit()
                conn.close()
                return {
                    "created": False,
                    "blocked": True,
                    "reason": "LIVE_AMBIGUOUS_ORDER_PENDING",
                }
            exposure = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(amount_usd), 0) "
                "FROM execution_orders "
                "WHERE mode = 'live' AND side = 'buy' AND ts_created >= ?",
                (float(canary_limits["start_of_day_ts"]),),
            ).fetchone()
            if int(exposure[0] or 0) >= int(canary_limits["max_buys"]):
                conn.commit()
                conn.close()
                return {
                    "created": False,
                    "blocked": True,
                    "reason": "LIVE_CANARY_BUY_LIMIT_REACHED",
                }
            trader_exposure = conn.execute(
                "SELECT COUNT(*) FROM execution_orders "
                "WHERE mode = 'live' AND side = 'buy' AND ts_created >= ? "
                "AND origin_trader = ?",
                (
                    float(canary_limits["start_of_day_ts"]),
                    canary_trader,
                ),
            ).fetchone()
            if (
                int(trader_exposure[0] or 0)
                >= LIVE_CANARY_HARD_MAX_BUYS_PER_TRADER_PER_DAY
            ):
                conn.commit()
                conn.close()
                return {
                    "created": False,
                    "blocked": True,
                    "reason": "LIVE_CANARY_TRADER_BUY_LIMIT_REACHED",
                }
            if (
                float(exposure[1] or 0) + float(amount_usd)
                > float(canary_limits["max_notional_usd"])
            ):
                conn.commit()
                conn.close()
                return {
                    "created": False,
                    "blocked": True,
                    "reason": "LIVE_CANARY_NOTIONAL_LIMIT_REACHED",
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
        mode,
        origin_trader
    )
    VALUES(
        ?,?,?,?,?,?,?,?,?,?,?,?,?,?
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
        mode,
        (
            str(canary_limits.get("trader") or "").strip()
            if canary_limits is not None
            else None
        ),
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
    try:
        now = time.time()
        cursor = conn.execute(
            "UPDATE execution_orders SET ts_updated = ?, status = ?, reason = ? "
            "WHERE id = ? AND (? != 'PENDING_RECONCILIATION' "
            "OR status IN ('SENT', 'PENDING_RECONCILIATION'))",
            (now, status, reason, order_id, status),
        )
        if cursor.rowcount:
            conn.execute(
                "INSERT INTO execution_order_events(order_id, ts, status, reason) "
                "VALUES (?, ?, ?, ?)", (order_id, now, status, reason),
            )
        conn.commit()
    finally:
        conn.close()


def set_execution_order_external_signature(order_id, signature):
    signature = normalize_solana_signature(signature)

    conn = db()
    cursor = conn.execute(
        """
        UPDATE execution_orders
        SET external_signature = ?, ts_updated = ?
        WHERE id = ?
        AND (external_signature IS NULL OR external_signature = ?)
        AND status IN ('SENT', 'PENDING_RECONCILIATION')
        """,
        (signature, time.time(), order_id, signature),
    )
    conn.commit()
    updated = cursor.rowcount == 1
    conn.close()
    return updated

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
        "KILL_SWITCH",
        "INVALID_AMOUNT_USD"
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

    retry_result = execute_order(
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

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT status, mode FROM execution_orders WHERE id = ?", (order_id,),
        ).fetchone()
        if not current:
            return {"ok": False, "reason": "ORDER_NOT_FOUND"}
        if current[0] not in ("SENT", "PENDING_RECONCILIATION"):
            return {"ok": False, "reason": "ORDER_NOT_PENDING", "status": current[0]}
        if final_status == "CONFIRMED" and current[1] == "live":
            return {"ok": False, "reason": "LIVE_RECEIPT_REQUIRED"}
        now = time.time()
        conn.execute(
            "UPDATE execution_orders SET status = ?, ts_updated = ?, reason = ? "
            "WHERE id = ?", (final_status, now, reason, order_id),
        )
        conn.execute(
            "INSERT INTO execution_order_events(order_id, ts, status, reason) "
            "VALUES (?, ?, ?, ?)", (order_id, now, final_status, reason),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": final_status == "CONFIRMED",
        "order_id": order_id,
        "status": final_status,
        "reason": reason
    }


def fetch_finalized_solana_transaction(signature):
    signature = normalize_solana_signature(signature)
    if not signature:
        raise ValueError("INVALID_SIGNATURE")
    request = Request(
        SOLANA_RPC_URL,
        data=json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getTransaction",
            "params": [signature, {
                "encoding": "jsonParsed", "commitment": "finalized",
                "maxSupportedTransactionVersion": 0,
            }],
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    if (not isinstance(payload, dict) or payload.get("error")
            or "result" not in payload):
        raise ValueError("INVALID_SOLANA_RPC_RESPONSE")
    receipt = payload["result"]
    if receipt is not None and not isinstance(receipt, dict):
        raise ValueError("INVALID_SOLANA_RECEIPT")
    return receipt


def record_finalized_buy_position(order_id, fill, receipt):
    entry_block_time = validated_block_event_ts(
        fill.get("block_time"),
        origen="buy_receipt.block_time",
    )
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, external_signature, trade_wallet, mint, side, source, mode, "
            "entry_market_cap_sol, origin_trader "
            "FROM execution_orders WHERE id = ?", (order_id,),
        ).fetchone()
        if (not row or row[4:7] != ("buy", "pumpportal_lightning", "live")
                or row[1:4] != (fill["signature"], fill["wallet"], fill["mint"])):
            raise ValueError("ORDER_RECEIPT_IDENTITY_MISMATCH")
        verified = parse_buy_receipt(receipt, row[1], row[2], row[3])
        if verified != fill:
            raise ValueError("ORDER_RECEIPT_FILL_MISMATCH")
        existing = conn.execute(
            "SELECT signature FROM live_positions WHERE order_id = ?", (order_id,),
        ).fetchone()
        if existing and existing[0] == row[1] and row[0] == "CONFIRMED":
            conn.rollback()
            if row[3] and not row[3].startswith("DEMO"):
                TRACKED_TOKENS.add(row[3])
            return {"ok": True, "order_id": order_id, "status": "CONFIRMED"}
        if existing or row[0] not in ("SENT", "PENDING_RECONCILIATION"):
            raise ValueError("ORDER_NOT_PENDING")
        now = time.time()
        conn.execute(
            """INSERT INTO live_positions(
                order_id, signature, wallet, mint, token_amount_raw,
                remaining_amount_raw, token_decimals, net_sol_debit_lamports,
                network_fee_lamports, cash_cost_per_token_sol,
                remaining_cost_basis_lamports,
                entry_market_cap_sol, current_market_cap_sol, origin_trader,
                entry_block_time, last_applied_block_event_ts,
                fill_json, receipt_json, recorded_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, row[1], row[2], row[3], fill["token_amount_raw"],
             fill["token_amount_raw"], fill["token_decimals"],
             fill["net_sol_debit_lamports"], fill["network_fee_lamports"],
              fill["cash_cost_per_token_sol"], fill["net_sol_debit_lamports"],
              row[7], row[7], row[8],
              entry_block_time, entry_block_time,
              json.dumps(fill), json.dumps(receipt), now),
        )
        conn.execute(
            "UPDATE execution_orders SET status = 'CONFIRMED', ts_updated = ?, "
            "reason = 'SOLANA_RECEIPT_RECORDED' WHERE id = ?", (now, order_id),
        )
        conn.execute(
            "INSERT INTO execution_order_events(order_id, ts, status, reason) "
            "VALUES (?, ?, 'CONFIRMED', 'SOLANA_RECEIPT_RECORDED')", (order_id, now),
        )
        conn.commit()
        if row[3] and not row[3].startswith("DEMO"):
            TRACKED_TOKENS.add(row[3])
        return {"ok": True, "order_id": order_id, "status": "CONFIRMED",
                "reason": "SOLANA_RECEIPT_RECORDED"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_finalized_sell_position(order_id, fill, receipt):
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute(
            "SELECT status, external_signature, trade_wallet, mint, side, source, mode, "
            "parent_order_id, requested_token_amount_raw, exit_reason, target_tp_stage "
            "FROM execution_orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if (not order or order[4:7] != ("sell", "pumpportal_lightning", "live")
                or order[1:4] != (fill["signature"], fill["wallet"], fill["mint"])):
            raise ValueError("ORDER_RECEIPT_IDENTITY_MISMATCH")
        verified = parse_sell_receipt(receipt, order[1], order[2], order[3])
        if verified != fill:
            raise ValueError("ORDER_RECEIPT_FILL_MISMATCH")
        existing = conn.execute(
            "SELECT signature FROM live_position_sales WHERE sell_order_id = ?", (order_id,),
        ).fetchone()
        if existing and existing[0] == order[1] and order[0] == "CONFIRMED":
            conn.rollback()
            return {"ok": True, "order_id": order_id, "status": "CONFIRMED"}
        if existing or order[0] not in ("SENT", "PENDING_RECONCILIATION"):
            raise ValueError("ORDER_NOT_PENDING")
        position = conn.execute(
            "SELECT wallet, mint, status, remaining_amount_raw, token_decimals, "
            "remaining_cost_basis_lamports, tp_stage "
            "FROM live_positions WHERE order_id = ?",
            (order[7],),
        ).fetchone()
        sold = int(fill["token_amount_raw"])
        if (not position or position[0:2] != (order[2], order[3])
                or position[2] != "open" or fill["token_decimals"] != position[4]
                or str(sold) != order[8]):
            raise ValueError("SELL_POSITION_MISMATCH")
        remaining = int(position[3])
        if sold > remaining:
            raise ValueError("SELL_AMOUNT_EXCEEDS_POSITION")
        remaining_cost = int(position[5])
        allocated_cost = (
            remaining_cost if sold == remaining else remaining_cost * sold // remaining
        )
        proceeds = int(fill["net_sol_credit_lamports"])
        realized = proceeds - allocated_cost
        new_remaining = remaining - sold
        new_cost = remaining_cost - allocated_cost
        new_tp_stage = max(int(position[6] or 0), int(order[10] or 0))
        now = time.time()
        conn.execute(
            """INSERT INTO live_position_sales(
                sell_order_id, position_order_id, signature, token_amount_raw,
                net_sol_credit_lamports, network_fee_lamports,
                allocated_cost_basis_lamports, realized_pnl_lamports,
                block_time, fill_json, receipt_json, recorded_ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, order[7], order[1], str(sold), str(proceeds),
             fill["network_fee_lamports"], str(allocated_cost), str(realized),
             fill.get("block_time"), json.dumps(fill), json.dumps(receipt), now),
        )
        conn.execute(
            "UPDATE live_positions SET remaining_amount_raw = ?, "
            "remaining_cost_basis_lamports = ?, status = ?, tp_stage = ?, "
            "last_exit_reason = ? WHERE order_id = ?",
            (str(new_remaining), str(new_cost), "closed" if not new_remaining else "open",
             new_tp_stage, order[9] or None, order[7]),
        )
        conn.execute(
            "UPDATE execution_orders SET status = 'CONFIRMED', ts_updated = ?, "
            "reason = 'SOLANA_SELL_RECEIPT_RECORDED' WHERE id = ?", (now, order_id),
        )
        conn.execute(
            "INSERT INTO execution_order_events(order_id, ts, status, reason) "
            "VALUES (?, ?, 'CONFIRMED', 'SOLANA_SELL_RECEIPT_RECORDED')", (order_id, now),
        )
        conn.commit()
        if not new_remaining:
            try:
                untrack_token_if_unused(order[3])
            except Exception as exc:
                print(f"[LIVE TRACKING] Cleanup failed: {exc}")
        return {"ok": True, "order_id": order_id, "status": "CONFIRMED",
                "reason": "SOLANA_SELL_RECEIPT_RECORDED",
                "position_status": "closed" if not new_remaining else "open",
                "realized_pnl_lamports": str(realized)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reconcile_pumpportal_execution_order(order_id):
    conn = db()
    row = conn.execute(
        """
        SELECT status, external_signature, trade_wallet, mint, side, source, mode,
               parent_order_id, requested_token_amount_raw
        FROM execution_orders
        WHERE id = ?
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    conn.close()

    if not row:
        return {"ok": False, "reason": "ORDER_NOT_FOUND"}
    if row[4] not in ("buy", "sell") or row[5:7] != ("pumpportal_lightning", "live"):
        return {"ok": False, "reason": "UNSUPPORTED_RECEIPT_ORDER"}
    if row[0] not in ("SENT", "PENDING_RECONCILIATION"):
        return {
            "ok": False,
            "reason": "ORDER_NOT_PENDING",
            "status": row[0],
        }
    if not row[1]:
        return {"ok": False, "reason": "ORDER_SIGNATURE_MISSING"}

    try:
        chain_status = fetch_solana_signature_status(row[1])
    except Exception:
        return {
            "ok": False,
            "order_id": order_id,
            "status": "PENDING_RECONCILIATION",
            "reason": "SOLANA_STATUS_CHECK_FAILED",
        }
    if chain_status["failed"] and chain_status.get("confirmation_status") == "finalized":
        return reconcile_execution_order(
            order_id,
            "FAILED",
            "SOLANA_TRANSACTION_FAILED",
        )
    if chain_status["finalized"]:
        if not row[2]:
            return {"ok": False, "reason": "ORDER_TRADE_WALLET_MISSING"}
        try:
            receipt = fetch_finalized_solana_transaction(row[1])
            if receipt is None:
                return {"ok": False, "reason": "SOLANA_RECEIPT_NOT_AVAILABLE"}
            if row[4] == "buy":
                fill = parse_buy_receipt(receipt, row[1], row[2], row[3])
                return record_finalized_buy_position(order_id, fill, receipt)
            if row[7] is None or row[8] is None:
                return {"ok": False, "reason": "SELL_POSITION_REFERENCE_MISSING"}
            fill = parse_sell_receipt(receipt, row[1], row[2], row[3])
            return record_finalized_sell_position(order_id, fill, receipt)
        except Exception:
            return {
                "ok": False,
                "order_id": order_id,
                "status": "PENDING_RECONCILIATION",
                "reason": "SOLANA_RECEIPT_REVIEW_REQUIRED",
            }

    return {
        "ok": False,
        "order_id": order_id,
        "status": "PENDING_RECONCILIATION",
        "reason": "SOLANA_TRANSACTION_PENDING",
    }


def reconcile_pending_pumpportal_execution_orders(limit=20):
    conn = db()
    rows = conn.execute(
        """
        SELECT id
        FROM execution_orders
        WHERE source = 'pumpportal_lightning'
        AND status IN ('SENT', 'PENDING_RECONCILIATION')
        AND external_signature IS NOT NULL
        ORDER BY id ASC
        LIMIT ?
        """,
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    conn.close()
    return [
        reconcile_pumpportal_execution_order(row[0])
        for row in rows
    ]


def get_ambiguous_pumpportal_execution_orders(limit=20, now=None):
    """Return live orders that cannot be reconciled without manual review."""
    current_ts = float(now if now is not None else time.time())
    sent_cutoff = current_ts - EXECUTION_TIMEOUT_SECONDS
    conn = db()
    try:
        rows = conn.execute(
            """
            SELECT id, side, mint, status, reason, ts_created, ts_updated
            FROM execution_orders
            WHERE source = 'pumpportal_lightning'
            AND mode = 'live'
            AND external_signature IS NULL
            AND (
                status = 'PENDING_RECONCILIATION'
                OR (status = 'SENT' AND ts_updated <= ?)
            )
            ORDER BY id ASC
            LIMIT ?
            """,
            (sent_cutoff, max(1, min(int(limit), 100))),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "order_id": int(row[0]),
            "side": str(row[1] or ""),
            "mint": str(row[2] or ""),
            "status": str(row[3] or ""),
            "reason": str(row[4] or ""),
            "ts_created": float(row[5] or 0),
            "ts_updated": float(row[6] or 0),
        }
        for row in rows
    ]


async def maybe_send_ambiguous_execution_order_alerts(limit=20, now=None):
    """Alert once per live order that PumpPortal left without a signature."""
    if not DISCORD_ALERT_WEBHOOK_URL:
        return 0

    sent_count = 0
    for order in get_ambiguous_pumpportal_execution_orders(limit=limit, now=now):
        state_key = f"LIVE_AMBIGUOUS_ORDER_ALERT:{order['order_id']}"
        conn = db()
        try:
            already_sent = conn.execute(
                "SELECT 1 FROM app_state WHERE key = ? LIMIT 1",
                (state_key,),
            ).fetchone()
        finally:
            conn.close()
        if already_sent:
            continue

        sent = await send_discord_alert(
            "Pump Copilot CRITICAL: live PumpPortal order requires manual review.\n"
            f"Order: {order['order_id']} | Side: {order['side']} | "
            f"Status: {order['status']}\n"
            f"Mint: {order['mint']}\n"
            f"Reason: {order['reason'] or 'NO_SIGNATURE_RETURNED'}\n"
            "PumpPortal returned no usable transaction signature. New live buys "
            "are blocked automatically. Do not retry the order until wallet and "
            "on-chain activity have been reviewed."
        )
        if not sent:
            continue

        conn = db()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO app_state(key, value) VALUES(?, ?)",
                (state_key, str(time.time())),
            )
            conn.commit()
        finally:
            conn.close()
        sent_count += 1
    return sent_count


async def pumpportal_execution_reconciliation_worker():
    while True:
        try:
            await asyncio.to_thread(
                reconcile_pending_pumpportal_execution_orders
            )
        except Exception as ex:
            print("[EXECUTION RECONCILIATION ERROR]", repr(ex))

        try:
            await maybe_send_ambiguous_execution_order_alerts()
        except Exception as ex:
            print("[EXECUTION ALERT ERROR]", repr(ex))

        await asyncio.sleep(EXECUTION_RECONCILIATION_SECONDS)

def risk_check(
    mint,
    amount_usd,
    expected_price=None,
    execution_price=None,
    liquidity_sol=None,
    mode="paper",
    execution_order_id=None,
):

    if KILL_SWITCH:
        return {
            "ok": False,
            "reason": "KILL_SWITCH"
        }

    try:
        amount_usd = float(amount_usd)
    except (TypeError, ValueError):
        amount_usd = 0.0

    if not math.isfinite(amount_usd) or amount_usd <= 0:
        return {
            "ok": False,
            "reason": "INVALID_AMOUNT_USD"
        }

    if amount_usd > MAX_POSITION_USD:
        return {
            "ok": False,
            "reason": "MAX_POSITION_USD"
        }

    if mode == "live":
        daily_pnl_sol = get_daily_live_realized_pnl_sol()
        if daily_pnl_sol < 0:
            try:
                daily_pnl = daily_pnl_sol * fetch_sol_usd_quote()["price"]
            except Exception:
                return {
                    "ok": False,
                    "reason": "SOL_USD_QUOTE_UNAVAILABLE"
                }
        else:
            daily_pnl = 0.0
    else:
        daily_pnl = get_daily_realized_pnl(mode=mode)

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

    if mode == "live" and execution_order_id is not None:
        conn = db()
        earlier_order = conn.execute(
            """
            SELECT 1
            FROM execution_orders
            WHERE mode = 'live'
            AND id < ?
            AND status IN (
                'CREATED',
                'RISK_CHECKED',
                'SENT',
                'PENDING_RECONCILIATION'
            )
            LIMIT 1
            """,
            (execution_order_id,),
        ).fetchone()
        conn.close()
        if earlier_order:
            return {
                "ok": False,
                "reason": "LIVE_EXECUTION_IN_PROGRESS",
            }

    return {
        "ok": True,
        "reason": "RISK_OK",
        "mint": mint
    }


# =========================================================
# MIGRACIÓN DE LA BD VIEJA
# =========================================================

def migrate_shadow_predictions_for_multiple_models(conn):
    indexes = conn.execute(
        "PRAGMA index_list(model_shadow_predictions)"
    ).fetchall()
    unique_indexes = [row[1] for row in indexes if int(row[2]) == 1]
    unique_columns = [
        [
            column[2]
            for column in conn.execute(
                f'PRAGMA index_info("{index_name}")'
            ).fetchall()
        ]
        for index_name in unique_indexes
    ]

    if ["evaluation_id", "model_version"] in unique_columns:
        return False

    conn.execute("DROP TABLE IF EXISTS model_shadow_predictions_v2")
    conn.execute(
        """
        CREATE TABLE model_shadow_predictions_v2(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluation_id INTEGER NOT NULL,
            created_ts REAL NOT NULL,
            model_version TEXT NOT NULL,
            data_version INTEGER NOT NULL,
            probability REAL NOT NULL,
            threshold REAL NOT NULL,
            predicted_target INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            UNIQUE(evaluation_id, model_version)
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO model_shadow_predictions_v2(
            id,
            evaluation_id,
            created_ts,
            model_version,
            data_version,
            probability,
            threshold,
            predicted_target,
            features_json
        )
        SELECT
            id,
            evaluation_id,
            created_ts,
            model_version,
            data_version,
            probability,
            threshold,
            predicted_target,
            features_json
        FROM model_shadow_predictions
        """
    )
    conn.execute("DROP TABLE model_shadow_predictions")
    conn.execute(
        "ALTER TABLE model_shadow_predictions_v2 "
        "RENAME TO model_shadow_predictions"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_shadow_predictions_created_ts
        ON model_shadow_predictions(created_ts)
        """
    )
    return True


def migrate_token_history_identity(conn):
    """Le da identidad a las filas de historial anteriores a `event_id`.

    Sin esto, reproducir un evento viejo desde la cola escribiría de nuevo una
    fila que ya existe: las filas anteriores quedaron en NULL y el índice único
    es parcial, así que no las cubre.

    Migra **una sola fila por firma** a `firma:0`. Si hubiera firmas repetidas
    de antes de que existiera cualquier deduplicación, migrar todas violaría el
    índice único; migrando la primera, la identidad queda ocupada y un replay
    encuentra su duplicado igual. `UPDATE OR IGNORE` cubre el caso restante:
    una fila vieja cuya identidad ya se la llevó una fila nueva.

    Devuelve cuántas filas recibieron identidad.
    """
    columnas = [
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(token_history)"
        ).fetchall()
    ]

    if "event_id" not in columnas:
        conn.execute("ALTER TABLE token_history ADD COLUMN event_id TEXT")

    # Barato de saltear cuando no hay nada que migrar, que es el caso normal:
    # el backfill recorre la tabla y esto corre en cada arranque.
    pendiente = conn.execute(
        """
        SELECT 1
        FROM token_history
        WHERE event_id IS NULL
        AND COALESCE(signature, '') != ''
        LIMIT 1
        """
    ).fetchone()

    migradas = 0

    if pendiente:
        cursor = conn.execute(
            """
            UPDATE OR IGNORE token_history
            SET event_id = signature || ':0'
            WHERE event_id IS NULL
            AND COALESCE(signature, '') != ''
            AND id IN (
                SELECT MIN(id)
                FROM token_history
                WHERE COALESCE(signature, '') != ''
                AND event_id IS NULL
                GROUP BY signature
            )
            """
        )
        migradas = cursor.rowcount

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_token_history_event_id
        ON token_history(event_id)
        WHERE event_id IS NOT NULL
        """
    )

    return migradas


def migrate_helius_webhook_sync_state(conn):
    """Crea el estado de propiedad para la sincronización del webhook."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS helius_webhook_sync_state(
            id INTEGER PRIMARY KEY CHECK(id = 1),
            initialized INTEGER NOT NULL DEFAULT 0,
            base_addresses_json TEXT NOT NULL DEFAULT '[]',
            managed_tokens_json TEXT NOT NULL DEFAULT '[]',
            pending_tokens_json TEXT NOT NULL DEFAULT '[]',
            last_remote_addresses_json TEXT NOT NULL DEFAULT '[]',
            last_desired_addresses_json TEXT NOT NULL DEFAULT '[]',
            last_check_ts REAL,
            last_success_ts REAL,
            last_update_ts REAL,
            last_error TEXT,
            updates INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO helius_webhook_sync_state(id)
        VALUES(1)
        """
    )


def migrate_processed_market_events(conn):
    """Convierte la deduplicación histórica por firma a identidad completa."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_market_events(
            event_id TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            ts REAL,
            source TEXT DEFAULT 'live'
        )
        """
    )
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO processed_market_events(
            event_id, signature, event_index, ts, source
        )
        SELECT signature || ':0', signature, 0, ts, source
        FROM processed_signatures
        WHERE COALESCE(signature, '') != ''
        """
    )
    return cursor.rowcount


def migrate_inbox_event_index_to_ordinal(conn):
    """Renumera el inbox de índice de log a ordinal de operación.

    Las filas guardadas antes de este cambio llevan la posición dentro de
    `logMessages`, que depende del proveedor: para una transacción de una sola
    operación da 1 por Helius y 0 por PumpPortal. Dejarlas así significaría que
    la misma operación tiene dos identidades según quién la trajo, y procesar
    el inbox histórico duplicaría lo que ya se guardó por el stream.

    Se renumera 0,1,2… por firma, respetando el orden que ya tenían. Como el
    ordinal nuevo nunca es mayor que el índice viejo, actualizar en orden
    ascendente no puede chocar con la clave primaria `(signature, event_index)`.

    Solo revisa firmas con filas marcadas en el esquema viejo. El valor por
    defecto sigue siendo `log-v1` para que una reversión temporal a código
    anterior deje filas detectables cuando vuelva esta versión.

    Devuelve cuántas filas cambiaron de índice.
    """
    firmas_pendientes = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT signature
            FROM market_event_inbox
            WHERE event_index_scheme = 'log-v1'
            """
        ).fetchall()
    ]

    renumeradas = 0

    for firma in firmas_pendientes:
        filas = conn.execute(
            """
            SELECT event_index, event_json
            FROM market_event_inbox
            WHERE signature = ?
            ORDER BY event_index
            """,
            (firma,),
        ).fetchall()

        for ordinal, (indice_viejo, event_json) in enumerate(filas):
            if ordinal == indice_viejo:
                conn.execute(
                    """
                    UPDATE market_event_inbox
                    SET event_index_scheme = 'ordinal-v1'
                    WHERE signature = ? AND event_index = ?
                    """,
                    (firma, indice_viejo),
                )
                continue

            try:
                evento = json.loads(event_json)
            except (TypeError, ValueError):
                evento = None

            if isinstance(evento, dict):
                evento["eventIndex"] = ordinal
                event_json = json.dumps(
                    evento, sort_keys=True, separators=(",", ":")
                )

            conn.execute(
                """
                UPDATE market_event_inbox
                SET event_index = ?, event_index_scheme = 'ordinal-v1',
                    event_json = ?
                WHERE signature = ? AND event_index = ?
                """,
                (ordinal, event_json, firma, indice_viejo),
            )
            renumeradas += 1

    return renumeradas


def migrate_market_event_inbox_index_scheme(conn):
    """Versiona el formato del índice sin barrer el inbox en cada arranque."""
    columnas = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(market_event_inbox)"
        ).fetchall()
    }

    if "event_index_scheme" not in columnas:
        conn.execute(
            "ALTER TABLE market_event_inbox "
            "ADD COLUMN event_index_scheme TEXT NOT NULL DEFAULT 'log-v1'"
        )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_event_inbox_index_scheme
        ON market_event_inbox(event_index_scheme, signature, event_index)
        """
    )


def migrate_market_event_inbox_validation(conn):
    """Añade el estado de reserva que necesita el validador del inbox."""
    columnas = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(market_event_inbox)"
        ).fetchall()
    }

    migrations = {
        "claim_token": (
            "ALTER TABLE market_event_inbox ADD COLUMN claim_token TEXT"
        ),
        "claimed_ts": (
            "ALTER TABLE market_event_inbox ADD COLUMN claimed_ts REAL"
        ),
    }

    for column, statement in migrations.items():
        if column not in columnas:
            conn.execute(statement)

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_event_inbox_validation
        ON market_event_inbox(status, claimed_ts, received_ts)
        """
    )


def migrate_evaluation_event_identity(conn):
    """Migra evaluaciones de firma única a identidad firma + índice."""
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(evaluations)").fetchall()
    }
    if not columns:
        return

    has_composite_unique = False
    for index_row in conn.execute("PRAGMA index_list(evaluations)").fetchall():
        if not index_row[2]:
            continue
        index_columns = [
            row[2]
            for row in conn.execute(
                f'PRAGMA index_info("{index_row[1]}")'
            ).fetchall()
        ]
        if index_columns == ["trade_signature", "event_index"]:
            has_composite_unique = True
            break

    if "event_index" in columns and has_composite_unique:
        return

    legacy_event_index = "event_index" if "event_index" in columns else "0"

    conn.commit()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE evaluations_identity_v2(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_signature TEXT,
                event_index INTEGER NOT NULL DEFAULT 0,
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
                reasons TEXT,
                market_cap REAL DEFAULT 0,
                sol_amount REAL DEFAULT 0,
                data_version INTEGER DEFAULT 0,
                UNIQUE(trade_signature, event_index)
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO evaluations_identity_v2(
                id, trade_signature, event_index, ts, trader, mint, source,
                score, decision, trader_score, timing_score, size_score,
                token_score, consensus_score, market_score, reasons,
                market_cap, sol_amount, data_version
            )
            SELECT
                id, trade_signature, {legacy_event_index},
                ts, trader, mint, source,
                score, decision, trader_score, timing_score, size_score,
                token_score, consensus_score, market_score, reasons,
                market_cap, sol_amount, data_version
            FROM evaluations
            """
        )
        conn.execute("DROP TABLE evaluations")
        conn.execute(
            "ALTER TABLE evaluations_identity_v2 RENAME TO evaluations"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate_rpc_fallback_balance_nullable(conn):
    """Permite `NULL` en `rpc_fallback_events.new_token_balance`.

    La columna nació `NOT NULL` cuando el parser todavía convertía un saldo
    irrecuperable en cero. Desde que lo entrega como ``None``, cada evento con
    saldo desconocido hacía fallar el worker del fallback en producción y la
    auditoría quedaba ciega. SQLite no cambia restricciones con ALTER, así que
    se reconstruye la tabla conservando IDs; idempotente.
    """
    columns = conn.execute(
        "PRAGMA table_info(rpc_fallback_events)"
    ).fetchall()
    if not columns:
        return

    not_null = {row[1]: bool(row[3]) for row in columns}
    if not not_null.get("new_token_balance", False):
        return

    conn.commit()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE rpc_fallback_events_nullable_balance(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature TEXT NOT NULL,
                event_index INTEGER NOT NULL,
                slot INTEGER,
                block_time REAL,
                detected_ts REAL NOT NULL,
                trader TEXT NOT NULL,
                wallet TEXT NOT NULL,
                program TEXT NOT NULL,
                event_name TEXT NOT NULL,
                side TEXT NOT NULL,
                mint TEXT NOT NULL,
                sol REAL NOT NULL,
                market_cap_sol REAL NOT NULL,
                token_amount REAL NOT NULL,
                new_token_balance REAL,
                pool TEXT NOT NULL,
                event_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                alerted_ts REAL,
                UNIQUE(signature, event_index, wallet)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO rpc_fallback_events_nullable_balance(
                id, signature, event_index, slot, block_time, detected_ts,
                trader, wallet, program, event_name, side, mint, sol,
                market_cap_sol, token_amount, new_token_balance, pool,
                event_json, status, alerted_ts
            )
            SELECT
                id, signature, event_index, slot, block_time, detected_ts,
                trader, wallet, program, event_name, side, mint, sol,
                market_cap_sol, token_amount, new_token_balance, pool,
                event_json, status, alerted_ts
            FROM rpc_fallback_events
            """
        )
        conn.execute("DROP TABLE rpc_fallback_events")
        conn.execute(
            "ALTER TABLE rpc_fallback_events_nullable_balance "
            "RENAME TO rpc_fallback_events"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate_database():

    conn = db()

    conn.execute(
        "PRAGMA journal_mode = WAL"
    )

    migrate_shadow_predictions_for_multiple_models(conn)
    migrate_helius_webhook_sync_state(conn)
    migrate_processed_market_events(conn)
    migrate_token_history_identity(conn)
    migrate_market_event_inbox_index_scheme(conn)
    migrate_inbox_event_index_to_ordinal(conn)
    migrate_market_event_inbox_validation(conn)

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
            "ALTER TABLE paper_positions ADD COLUMN exit_reason TEXT DEFAULT ''",

        # Sin DEFAULT a propósito: las posiciones que ya existen quedan en NULL,
        # que es la verdad —no sabemos su momento on-chain de entrada— y apaga
        # la guarda para ellas en vez de inventarles una referencia.
        "entry_block_event_ts":
            "ALTER TABLE paper_positions ADD COLUMN entry_block_event_ts REAL",

        "last_applied_block_event_ts":
            "ALTER TABLE paper_positions ADD COLUMN last_applied_block_event_ts REAL"
    }

    for column, sql in paper_migrations.items():

        if column not in existing_paper:

            try:
                conn.execute(sql)

            except Exception:
                pass


    existing_outcomes = [
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(signal_outcomes)"
        ).fetchall()
    ]

    outcome_migrations = {
        "tp25_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN tp25_ts REAL",

        "tp50_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN tp50_ts REAL",

        "sl10_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN sl10_ts REAL",

        "status":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN status TEXT DEFAULT 'active'",

        "observed_10s_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN observed_10s_ts REAL",

        "observed_30s_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN observed_30s_ts REAL",

        "observed_1m_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN observed_1m_ts REAL",

        "observed_5m_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN observed_5m_ts REAL",

        "observed_15m_ts":
            "ALTER TABLE signal_outcomes "
            "ADD COLUMN observed_15m_ts REAL",
    }

    for column, sql in outcome_migrations.items():

        if column not in existing_outcomes:

            try:
                conn.execute(sql)

            except Exception:
                pass


    existing_evaluations = [
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(evaluations)"
        ).fetchall()
    ]

    evaluation_migrations = {
        "market_cap":
            "ALTER TABLE evaluations "
            "ADD COLUMN market_cap REAL DEFAULT 0",

        "sol_amount":
            "ALTER TABLE evaluations "
            "ADD COLUMN sol_amount REAL DEFAULT 0",

        "data_version":
            "ALTER TABLE evaluations "
            "ADD COLUMN data_version INTEGER DEFAULT 0",    
    }

    for column, sql in evaluation_migrations.items():

        if column not in existing_evaluations:

            try:
                conn.execute(sql)

            except Exception:
                pass

    migrate_evaluation_event_identity(conn)


    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rpc_fallback_wallet_state(
            wallet TEXT PRIMARY KEY,
            trader TEXT NOT NULL,
            last_signature TEXT,
            last_slot INTEGER,
            last_polled_ts REAL,
            baseline_ts REAL,
            last_error TEXT DEFAULT ''
        )
        """
    )

    try:
        conn.execute("ALTER TABLE rpc_fallback_wallet_state ADD COLUMN baseline_ts REAL")
    except Exception:
        pass
    conn.execute("UPDATE rpc_fallback_wallet_state SET baseline_ts = COALESCE(baseline_ts, ?)", (time.time(),))

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS rpc_fallback_events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signature TEXT NOT NULL,
            event_index INTEGER NOT NULL,
            slot INTEGER,
            block_time REAL,
            detected_ts REAL NOT NULL,
            trader TEXT NOT NULL,
            wallet TEXT NOT NULL,
            program TEXT NOT NULL,
            event_name TEXT NOT NULL,
            side TEXT NOT NULL,
            mint TEXT NOT NULL,
            sol REAL NOT NULL,
            market_cap_sol REAL NOT NULL,
            token_amount REAL NOT NULL,
            -- NULL es saldo desconocido: el parser no siempre puede
            -- reconstruirlo y convertirlo en cero fabricaría una salida total.
            new_token_balance REAL,
            pool TEXT NOT NULL,
            event_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            alerted_ts REAL,
            UNIQUE(signature, event_index, wallet)
        )
        """
    )

    # Antes de recrear el índice: la migración reconstruye la tabla y el DROP
    # se lleva el índice viejo.
    migrate_rpc_fallback_balance_nullable(conn)

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rpc_fallback_events_status
        ON rpc_fallback_events(status, detected_ts)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rpc_fallback_events_block_time "
        "ON rpc_fallback_events(block_time)"
    )


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
    source="live",
    event_index=0,
    event_ts=None,
):
    """Guarda un punto de historial de market cap.

    Con firma, la escritura es idempotente por evento: el mismo reintento no
    agrega una fila más. La identidad es `firma:índice`, así que dos
    operaciones Pump de una misma transacción son dos filas —antes la segunda
    se perdía, porque la deduplicación miraba solo la firma— y la misma
    operación traída por PumpPortal y por Helius es una sola.

    Con timestamp on-chain, el historial conserva el orden real aunque el
    webhook llegue atrasado. Sin firma o timestamp se conserva el
    comportamiento anterior para PumpPortal y las rutas de demo.
    """

    if not mint:
        return

    market_cap = float(
        market_cap or 0
    )

    if market_cap <= 0:
        return

    event_id = market_event_identity(signature, event_index)
    history_ts = validated_block_event_ts(event_ts, origen="event_ts")
    if history_ts is None:
        history_ts = time.time()

    conn = db()

    # `OR IGNORE` contra el índice único parcial, en vez de consultar y después
    # insertar: entre esas dos operaciones cabe otro escritor, y acá llegan
    # reintentos y dos proveedores que pueden traer la misma operación a la vez.
    # La deduplicación queda dentro de la misma sentencia que escribe.
    try:
        conn.execute("BEGIN IMMEDIATE")

        conn.execute(
            """
            INSERT OR IGNORE INTO token_history(
                ts,
                mint,
                market_cap_sol,
                trader,
                side,
                signature,
                source,
                event_id
            )

            VALUES(
                ?,?,?,?,?,?,?,?
            )
            """,
            (
                history_ts,
                mint,
                market_cap,
                trader,
                side,
                signature,
                source,
                event_id
            )
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
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
    details="",
    connection=None,
):
    """Registra un evento de posición.

    Con ``connection`` escribe dentro de la transacción del llamador y no
    confirma: así la auditoría y el efecto que la origina quedan atados. Si la
    auditoría quedara fuera y fallara, el reintento vería la aplicación ya
    registrada y el evento se perdería para siempre.
    """

    if not mint:
        return

    conn = connection or db()

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

    if connection is None:
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
    if not TRADER_DYNAMIC_QUALITY_ENABLED:
        if trader in OBSERVE_TRADERS:
            return 10

        return TRADER_QUALITY_NEUTRAL

    assessment = get_trader_quality_assessment(
        trader
    )

    return assessment["effective_quality"]

def clamp_trader_quality(value):
    return max(
        TRADER_QUALITY_MIN,
        min(
            TRADER_QUALITY_MAX,
            int(round(value))
        )
    )

def calculate_trader_quality_candidate(hit_stats):
    """Calidad por encogimiento continuo hacia el prior neutral.

    El posterior Beta ya encoge solo cuando hay pocos datos: con cero
    muestras devuelve exactamente el neutral (5 + 0.4 * 25 = 15) y con dos
    muestras se mueve apenas. Por eso el valor se calcula SIEMPRE y no
    depende de ningún umbral: un escalón en la muestra 30 movería al trader
    hasta 8 puntos de golpe sin justificación estadística.

    El mínimo de muestras ya no decide el valor, solo decide qué se publica
    como número frente a "Sin calificar" (ver `rated`).
    """
    samples = int(
        hit_stats.get("samples") or 0
    )

    target_1 = int(hit_stats.get("target_1") or 0)

    posterior_success_rate = beta_posterior_rate(target_1, samples)

    raw_quality = (
        TRADER_QUALITY_MIN
        + posterior_success_rate
        * (TRADER_QUALITY_MAX - TRADER_QUALITY_MIN)
    )

    rated = samples >= TRADER_QUALITY_MIN_SAMPLES

    return {
        "candidate_quality": clamp_trader_quality(
            raw_quality
        ),
        "raw_quality": round(raw_quality, 3),
        "posterior_success_rate": posterior_success_rate,
        "interval": wilson_interval(target_1, samples),
        "rated": rated,
        "ready_for_review": rated,
        "blockers": (
            []
            if rated
            else [f"samples {samples}/{TRADER_QUALITY_MIN_SAMPLES}"]
        ),
    }

def get_trader_quality_assessment(trader):
    if trader in OBSERVE_TRADERS:
        return {
            "trader": trader,
            "base_quality": 10,
            "effective_quality": 10,
            "candidate_quality": 10,
            "raw_quality": 10,
            "posterior_success_rate": 0.0,
            "interval": None,
            "rated": False,
            "dynamic_enabled": False,
            "ready_for_review": False,
            "blockers": ["observe_only"],
            "samples": 0,
            "tp25_pct": 0.0,
            "tp50_pct": 0.0,
            "sl10_pct": 0.0,
            "target_1": 0,
            "target_0": 0,
            "target_rate_pct": 0.0,
        }

    base_quality = TRADER_QUALITY_NEUTRAL

    hit_stats = get_trader_hit_stats(
        trader
    )

    candidate = calculate_trader_quality_candidate(hit_stats)

    # El valor ya no depende del umbral: el posterior encoge solo hacia el
    # neutral cuando hay poca evidencia, así que no hace falta sustituirlo
    # por una constante ni provocar un salto al cruzar el mínimo.
    effective_quality = (
        candidate["candidate_quality"]
        if TRADER_DYNAMIC_QUALITY_ENABLED
        else base_quality
    )

    return {
        "trader": trader,
        "base_quality": base_quality,
        "effective_quality": effective_quality,
        "candidate_quality": candidate["candidate_quality"],
        "raw_quality": candidate.get(
            "raw_quality",
            base_quality
        ),
        "posterior_success_rate": float(
            candidate.get("posterior_success_rate") or 0
        ),
        "interval": candidate.get("interval"),
        # "rated" distingue "sin medir" de "medido y resultó promedio":
        # ambos dan un número parecido, pero solo uno es una medición.
        "rated": bool(candidate["rated"]),
        "dynamic_enabled": bool(TRADER_DYNAMIC_QUALITY_ENABLED),
        "ready_for_review": candidate["ready_for_review"],
        "blockers": candidate["blockers"],
        "samples": int(hit_stats.get("samples") or 0),
        "tp25_pct": float(hit_stats.get("tp25_pct") or 0),
        "tp50_pct": float(hit_stats.get("tp50_pct") or 0),
        "sl10_pct": float(hit_stats.get("sl10_pct") or 0),
        "target_1": int(hit_stats.get("target_1") or 0),
        "target_0": int(hit_stats.get("target_0") or 0),
        "target_rate_pct": float(
            hit_stats.get("target_rate_pct") or 0
        ),
    }

def get_trader_copyability_stats(trader):
    conn = db()

    row = conn.execute(
        """
        SELECT
            COUNT(*),

            COUNT(return_10s),
            COUNT(return_30s),
            COUNT(return_1m),
            COUNT(return_5m),
            COUNT(return_15m),

            AVG(return_10s),
            AVG(return_30s),
            AVG(return_1m),
            AVG(return_5m),
            AVG(return_15m),

            MAX(max_return),
            MIN(min_return),

            AVG(
                CASE
                    WHEN return_5m > 0 THEN 1.0
                    WHEN return_5m IS NOT NULL THEN 0.0
                    ELSE NULL
                END
            )

        FROM signal_outcomes

        WHERE trader = ?
        AND price_at_signal > 0
AND status != 'expired'
        """,
        (trader,)
    ).fetchone()

    conn.close()

    return {
        "trader": trader,

        "signals": int(row[0] or 0),

        "samples_10s": int(row[1] or 0),
        "samples_30s": int(row[2] or 0),
        "samples_1m": int(row[3] or 0),
        "samples_5m": int(row[4] or 0),
        "samples_15m": int(row[5] or 0),

        "avg_return_10s": round(float(row[6] or 0), 2),
        "avg_return_30s": round(float(row[7] or 0), 2),
        "avg_return_1m": round(float(row[8] or 0), 2),
        "avg_return_5m": round(float(row[9] or 0), 2),
        "avg_return_15m": round(float(row[10] or 0), 2),

        "max_return": round(float(row[11] or 0), 2),
        "min_return": round(float(row[12] or 0), 2),

        "positive_5m_pct": round(
            float(row[13] or 0) * 100,
            2
        ),
    }

def get_trader_hit_stats(trader):
    conn = db()

    row = conn.execute(
        f"""
        WITH ranked_outcomes AS (
            SELECT
                max_return,
                min_return,
                hit_tp25,
                hit_tp50,
                hit_sl10,
                tp25_ts,
                sl10_ts,
                status,
                ROW_NUMBER() OVER (
                    PARTITION BY mint
                    ORDER BY signal_ts ASC, id ASC
                ) AS mint_signal_number
            FROM signal_outcomes
            WHERE trader = ?
            AND price_at_signal > 0
            AND {DECIDABLE_OUTCOME_SQL}
        )
        SELECT
            -- Una muestra por token decidible. No se exige max_return:
            -- un outcome cuya observación de precio se perdió sigue siendo
            -- evidencia si alcanzamos a ver el TP25 o el SL10.
            COUNT(*),

            SUM(
                CASE
                    WHEN max_return IS NOT NULL
                    AND hit_tp25 = 1
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN max_return IS NOT NULL
                    AND hit_tp50 = 1
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN min_return IS NOT NULL
                    AND hit_sl10 = 1
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN tp25_ts IS NOT NULL
                    AND (
                        sl10_ts IS NULL
                        OR tp25_ts < sl10_ts
                    )
                    THEN 1
                    ELSE 0
                END
            )

        FROM ranked_outcomes
        WHERE mint_signal_number = 1
        """,
        (trader,)
    ).fetchone()

    conn.close()

    samples = int(row[0] or 0)

    tp25_hits = int(row[1] or 0)
    tp50_hits = int(row[2] or 0)
    sl10_hits = int(row[3] or 0)
    target_1 = int(row[4] or 0)
    target_0 = samples - target_1

    if samples > 0:
        tp25_pct = tp25_hits / samples * 100
        tp50_pct = tp50_hits / samples * 100
        sl10_pct = sl10_hits / samples * 100
        target_rate_pct = target_1 / samples * 100
    else:
        tp25_pct = 0.0
        tp50_pct = 0.0
        sl10_pct = 0.0
        target_rate_pct = 0.0

    return {
        "trader": trader,
        "samples": samples,

        "tp25_hits": tp25_hits,
        "tp25_pct": round(tp25_pct, 2),

        "tp50_hits": tp50_hits,
        "tp50_pct": round(tp50_pct, 2),

        "sl10_hits": sl10_hits,
        "sl10_pct": round(sl10_pct, 2),

        "target_1": target_1,
        "target_0": target_0,
        "target_rate_pct": round(target_rate_pct, 2),
    }

# =========================================================
# PERFIL INTEGRAL DE CALIDAD DEL TRADER (SHADOW)
# =========================================================
# Reglas de datos que aplican a todo el bloque:
# - Una muestra por token: señales repetidas del mismo mint no son evidencia
#   independiente.
# - Las ventas parciales de un ciclo se agregan en un único resultado.
# - Lo que no se puede reconstruir de forma fiable se reporta como None
#   (no disponible), nunca como 0.


def wilson_interval(successes, total, z=1.96):
    """Intervalo de Wilson. Devuelve None si no hay muestras."""
    if total <= 0:
        return None

    successes = max(0, min(int(successes), int(total)))
    total = int(total)
    rate = successes / total

    denominator = 1 + (z * z) / total
    center = rate + (z * z) / (2 * total)
    spread = z * math.sqrt(
        (rate * (1 - rate) + (z * z) / (4 * total)) / total
    )

    lower = (center - spread) / denominator
    upper = (center + spread) / denominator

    return {
        "lower": max(0.0, lower),
        "upper": min(1.0, upper),
        "width": min(1.0, upper) - max(0.0, lower),
    }


def beta_posterior_rate(successes, total):
    """Media posterior Beta con el mismo prior neutral del score vigente."""
    return (
        TRADER_QUALITY_PRIOR_SUCCESSES + max(0, successes)
    ) / (
        TRADER_QUALITY_PRIOR_SUCCESSES
        + TRADER_QUALITY_PRIOR_FAILURES
        + max(0, total)
    )


def get_trader_entry_samples(trader, connection=None):
    """Una muestra por token: el primer outcome completado de cada mint."""
    conn = connection or db()

    try:
        rows = conn.execute(
            f"""
            WITH ranked_outcomes AS (
                SELECT
                    mint,
                    signal_ts,
                    max_return,
                    min_return,
                    return_5m,
                    tp25_ts,
                    sl10_ts,
                    ROW_NUMBER() OVER (
                        PARTITION BY mint
                        ORDER BY signal_ts ASC, id ASC
                    ) AS mint_signal_number
                FROM signal_outcomes
                WHERE trader = ?
                AND price_at_signal > 0
                AND {DECIDABLE_OUTCOME_SQL}
            )
            SELECT
                mint,
                signal_ts,
                max_return,
                min_return,
                return_5m,
                tp25_ts,
                sl10_ts
            FROM ranked_outcomes
            WHERE mint_signal_number = 1
            ORDER BY signal_ts ASC
            """,
            (trader,),
        ).fetchall()
    finally:
        if connection is None:
            conn.close()

    samples = []

    for row in rows:
        tp25_ts = row[5]
        sl10_ts = row[6]

        samples.append({
            "mint": row[0],
            "signal_ts": float(row[1] or 0),
            "max_return": (
                float(row[2]) if row[2] is not None else None
            ),
            "min_return": (
                float(row[3]) if row[3] is not None else None
            ),
            "return_5m": (
                float(row[4]) if row[4] is not None else None
            ),
            "tp25_first": bool(
                tp25_ts is not None
                and (sl10_ts is None or tp25_ts < sl10_ts)
            ),
            "sl10_first": bool(
                sl10_ts is not None
                and (tp25_ts is None or sl10_ts < tp25_ts)
            ),
        })

    return samples


def get_trader_exit_cycles(trader, connection=None):
    """Reconstruye ciclos entrada -> ventas por token.

    Solo se reconstruye un ciclo cuando observamos la entrada antes de
    cualquier venta. Si empezamos a mirar el token a mitad de su vida, el
    ciclo se descarta: no se puede saber a qué precio entró.

    Las ventas parciales del mismo ciclo se agregan ponderando por SOL
    recibido, de modo que cada ciclo aporta un único resultado.
    """
    conn = connection or db()

    try:
        rows = conn.execute(
            """
            SELECT
                mint,
                ts,
                side,
                sol,
                market_cap_sol,
                new_token_balance
            FROM trades
            WHERE trader = ?
            AND source = 'live'
            ORDER BY mint ASC, ts ASC, id ASC
            """,
            (trader,),
        ).fetchall()
    finally:
        if connection is None:
            conn.close()

    by_mint = {}

    for mint, ts, side, sol, market_cap_sol, new_token_balance in rows:
        by_mint.setdefault(str(mint or ""), []).append({
            "ts": float(ts or 0),
            "side": str(side or "").lower(),
            "sol": float(sol or 0),
            "market_cap_sol": float(market_cap_sol or 0),
            "new_token_balance": (
                float(new_token_balance)
                if new_token_balance is not None
                else None
            ),
        })

    cycles = []
    skipped_mid_life = 0
    skipped_no_entry_price = 0

    for mint, events in by_mint.items():
        if not mint:
            continue

        entry = None
        sells = []
        saw_positive_balance = False
        closure_confirmed = False

        for event in events:
            is_entry = (
                "buy" in event["side"]
                or event["side"] == "create"
            )

            if entry is None:
                if not is_entry:
                    # La primera vez que vemos el token ya estaba vendiendo:
                    # nunca observamos su entrada.
                    break

                if event["market_cap_sol"] <= 0:
                    break

                entry = event

                if (
                    event["new_token_balance"] is not None
                    and event["new_token_balance"] > 0
                ):
                    saw_positive_balance = True

                continue

            if (
                event["new_token_balance"] is not None
                and event["new_token_balance"] > 0
            ):
                saw_positive_balance = True

            if event["side"] != "sell":
                continue

            if event["market_cap_sol"] <= 0 or event["sol"] <= 0:
                continue

            sells.append(event)

            # new_token_balance == 0 es ambiguo (campo ausente o salida
            # total). Solo lo tomamos como cierre si antes vimos saldo
            # positivo en este mismo token.
            if (
                event["new_token_balance"] is not None
                and event["new_token_balance"] == 0
                and saw_positive_balance
            ):
                closure_confirmed = True

        if entry is None:
            first_event = events[0] if events else None

            if first_event is not None and first_event["side"] == "sell":
                skipped_mid_life += 1
            else:
                skipped_no_entry_price += 1

            continue

        if not sells:
            continue

        proceeds = sum(sell["sol"] for sell in sells)

        if proceeds <= 0:
            continue

        weighted_exit_mc = sum(
            sell["sol"] * sell["market_cap_sol"]
            for sell in sells
        ) / proceeds

        cycles.append({
            "mint": mint,
            "entry_ts": entry["ts"],
            "entry_market_cap_sol": entry["market_cap_sol"],
            "weighted_exit_market_cap_sol": weighted_exit_mc,
            "exit_ratio": (
                weighted_exit_mc / entry["market_cap_sol"] - 1
            ),
            "sell_events": len(sells),
            "sol_proceeds": proceeds,
            "closure_confirmed": closure_confirmed,
            "last_sell_ts": sells[-1]["ts"],
        })

    cycles.sort(key=lambda cycle: cycle["entry_ts"])

    return {
        "cycles": cycles,
        "skipped_mid_life": skipped_mid_life,
        "skipped_no_entry_price": skipped_no_entry_price,
    }


def summarize_trader_entry_quality(samples):
    """TP25 antes de SL10, separado del resto de dimensiones."""
    total = len(samples)

    if total == 0:
        return {
            "samples": 0,
            "tp25_first": None,
            "sl10_first": None,
            "tp25_first_rate": None,
            "posterior_rate": None,
            "interval": None,
        }

    tp25_first = sum(1 for sample in samples if sample["tp25_first"])
    sl10_first = sum(1 for sample in samples if sample["sl10_first"])

    return {
        "samples": total,
        "tp25_first": tp25_first,
        "sl10_first": sl10_first,
        "tp25_first_rate": tp25_first / total,
        "posterior_rate": beta_posterior_rate(tp25_first, total),
        "interval": wilson_interval(tp25_first, total),
    }


def summarize_trader_returns(samples):
    """Retornos y drawdown. Cada métrica es None si no hay datos."""
    max_returns = [
        sample["max_return"]
        for sample in samples
        if sample["max_return"] is not None
    ]
    min_returns = [
        sample["min_return"]
        for sample in samples
        if sample["min_return"] is not None
    ]
    returns_5m = [
        sample["return_5m"]
        for sample in samples
        if sample["return_5m"] is not None
    ]

    def average(values):
        return sum(values) / len(values) if values else None

    def median(values):
        return statistics.median(values) if values else None

    return {
        "max_return_samples": len(max_returns),
        "avg_max_return": average(max_returns),
        "median_max_return": median(max_returns),
        "drawdown_samples": len(min_returns),
        "avg_min_return": average(min_returns),
        "worst_min_return": min(min_returns) if min_returns else None,
        "return_5m_samples": len(returns_5m),
        "avg_return_5m": average(returns_5m),
        "positive_5m_rate": (
            sum(1 for value in returns_5m if value > 0) / len(returns_5m)
            if returns_5m
            else None
        ),
    }


def summarize_trader_consistency(samples):
    """Estabilidad del acierto entre la primera y la segunda mitad."""
    total = len(samples)
    half = total // 2

    if (
        total < TRADER_PROFILE_MIN_HALF_SAMPLES * 2
        or half < TRADER_PROFILE_MIN_HALF_SAMPLES
    ):
        return {
            "available": False,
            "reason": (
                "half_samples "
                f"{half}/{TRADER_PROFILE_MIN_HALF_SAMPLES}"
            ),
            "first_half_rate": None,
            "second_half_rate": None,
            "stability": None,
        }

    ordered = sorted(samples, key=lambda sample: sample["signal_ts"])
    first_half = ordered[:half]
    second_half = ordered[total - half:]

    first_rate = sum(
        1 for sample in first_half if sample["tp25_first"]
    ) / len(first_half)

    second_rate = sum(
        1 for sample in second_half if sample["tp25_first"]
    ) / len(second_half)

    return {
        "available": True,
        "reason": None,
        "first_half_rate": first_rate,
        "second_half_rate": second_rate,
        "stability": 1 - abs(first_rate - second_rate),
    }


def get_trader_activity_concentration(trader, connection=None):
    """Concentración de actividad entre tokens (Herfindahl)."""
    conn = connection or db()

    try:
        rows = conn.execute(
            """
            SELECT mint, COUNT(*)
            FROM trades
            WHERE trader = ?
            AND source = 'live'
            AND mint IS NOT NULL
            AND mint != ''
            GROUP BY mint
            """,
            (trader,),
        ).fetchall()
    finally:
        if connection is None:
            conn.close()

    counts = [int(row[1] or 0) for row in rows if int(row[1] or 0) > 0]
    total = sum(counts)

    if total <= 0 or not counts:
        return {
            "available": False,
            "distinct_tokens": 0,
            "events": 0,
            "hhi": None,
            "effective_tokens": None,
        }

    hhi = sum((count / total) ** 2 for count in counts)

    return {
        "available": True,
        "distinct_tokens": len(counts),
        "events": total,
        "hhi": hhi,
        "effective_tokens": 1 / hhi if hhi > 0 else None,
    }


def summarize_trader_recency(samples, now=None):
    """Peso de la evidencia según cuán reciente es."""
    if not samples:
        return {
            "available": False,
            "last_signal_ts": None,
            "last_signal_age_days": None,
            "recency_weighted_samples": None,
            "recency_ratio": None,
        }

    now = float(now if now is not None else time.time())
    half_life_seconds = TRADER_PROFILE_RECENCY_HALFLIFE_DAYS * 86400

    weighted = 0.0

    for sample in samples:
        age_seconds = max(0.0, now - sample["signal_ts"])
        weighted += 0.5 ** (age_seconds / half_life_seconds)

    last_signal_ts = max(sample["signal_ts"] for sample in samples)

    return {
        "available": True,
        "last_signal_ts": last_signal_ts,
        "last_signal_age_days": max(
            0.0,
            (now - last_signal_ts) / 86400,
        ),
        "recency_weighted_samples": weighted,
        "recency_ratio": weighted / len(samples),
    }


def summarize_trader_exit_quality(cycle_data):
    """Comportamiento de ventas. PnL absoluto queda no disponible.

    Los campos token_amount y new_token_balance no permiten reconstruir el
    tamaño real de la posición (los eventos 'create' no traen cantidad y un
    balance 0 es indistinguible de un campo ausente), así que se reporta la
    salida en términos de market cap relativo a la entrada y el PnL absoluto
    se marca explícitamente como no disponible.
    """
    cycles = cycle_data["cycles"]
    total = len(cycles)

    base = {
        "cycles": total,
        "skipped_mid_life": cycle_data["skipped_mid_life"],
        "skipped_no_entry_price": cycle_data["skipped_no_entry_price"],
        "confirmed_closures": sum(
            1 for cycle in cycles if cycle["closure_confirmed"]
        ),
        "realized_pnl_available": False,
        "realized_pnl_reason": "POSITION_SIZE_NOT_RECONSTRUCTABLE",
    }

    if total < TRADER_PROFILE_MIN_CYCLES:
        base.update({
            "available": False,
            "reason": f"cycles {total}/{TRADER_PROFILE_MIN_CYCLES}",
            "avg_exit_ratio": None,
            "median_exit_ratio": None,
            "positive_exit_rate": None,
            "partial_exit_rate": None,
        })

        return base

    exit_ratios = [cycle["exit_ratio"] for cycle in cycles]

    base.update({
        "available": True,
        "reason": None,
        "avg_exit_ratio": sum(exit_ratios) / total,
        "median_exit_ratio": statistics.median(exit_ratios),
        "positive_exit_rate": sum(
            1 for ratio in exit_ratios if ratio > 0
        ) / total,
        "partial_exit_rate": sum(
            1 for cycle in cycles if cycle["sell_events"] > 1
        ) / total,
    })

    return base


def summarize_trader_evidence(entry_quality, exit_quality, concentration):
    """Cantidad de evidencia y confianza asociada."""
    samples = int(entry_quality["samples"] or 0)
    interval = entry_quality["interval"]
    width = interval["width"] if interval else None

    if samples < TRADER_QUALITY_MIN_SAMPLES:
        label = "insuficiente"
    elif width is not None and width <= 0.20:
        label = "alta"
    elif width is not None and width <= 0.35:
        label = "media"
    else:
        label = "baja"

    return {
        "entry_samples": samples,
        "exit_cycles": int(exit_quality["cycles"] or 0),
        "distinct_tokens": int(concentration["distinct_tokens"] or 0),
        "minimum_entry_samples": TRADER_QUALITY_MIN_SAMPLES,
        "minimum_exit_cycles": TRADER_PROFILE_MIN_CYCLES,
        "interval_width": width,
        "confidence": label,
        "sufficient_evidence": samples >= TRADER_QUALITY_MIN_SAMPLES,
    }


def calculate_trader_profile_score(
    entry_quality,
    returns,
    exit_quality,
    consistency,
    concentration,
    recency,
    copyability,
):
    """Score integral 0-100. None cuando no hay evidencia suficiente.

    Cada componente aporta solo si está disponible y los pesos se
    renormalizan sobre los componentes presentes, para que un dato faltante
    no se cuente como un cero.
    """
    components = {}

    if entry_quality["posterior_rate"] is not None:
        components["entry_quality"] = entry_quality["posterior_rate"]

    if returns["positive_5m_rate"] is not None:
        components["returns"] = returns["positive_5m_rate"]

    if exit_quality["available"]:
        # Un ratio de salida de +50% o más satura el componente.
        components["exit_quality"] = max(
            0.0,
            min(1.0, (exit_quality["avg_exit_ratio"] + 0.5) / 1.0),
        )

    if consistency["available"]:
        components["consistency"] = max(
            0.0,
            min(1.0, consistency["stability"]),
        )

    if concentration["available"] and concentration["hhi"] is not None:
        components["diversification"] = max(
            0.0,
            min(1.0, 1 - concentration["hhi"]),
        )

    if recency["available"] and recency["recency_ratio"] is not None:
        components["recency"] = max(
            0.0,
            min(1.0, recency["recency_ratio"]),
        )

    if copyability["rate"] is not None:
        components["copyability"] = copyability["rate"]

    if not components:
        return {
            "score": None,
            "components": {},
            "weights_used": {},
        }

    total_weight = sum(
        TRADER_PROFILE_WEIGHTS[name]
        for name in components
    )

    if total_weight <= 0:
        return {
            "score": None,
            "components": components,
            "weights_used": {},
        }

    weights_used = {
        name: TRADER_PROFILE_WEIGHTS[name] / total_weight
        for name in components
    }

    score = sum(
        components[name] * weights_used[name]
        for name in components
    ) * 100

    return {
        "score": score,
        "components": components,
        "weights_used": weights_used,
    }


def summarize_trader_copyability(samples):
    """Qué proporción de señales quedó realmente observable a 5 minutos."""
    total = len(samples)

    if total == 0:
        return {"samples": 0, "observable": 0, "rate": None}

    observable = sum(
        1 for sample in samples if sample["return_5m"] is not None
    )

    return {
        "samples": total,
        "observable": observable,
        "rate": observable / total,
    }


def get_trader_quality_profile(trader, now=None, connection=None):
    """Perfil integral y observacional de un trader.

    No influye en score_trader(), decision_from_score() ni en ninguna ruta
    de ejecución: es material de revisión.
    """
    trader = str(trader or "").strip()

    conn = connection or db()

    try:
        samples = get_trader_entry_samples(trader, connection=conn)
        cycle_data = get_trader_exit_cycles(trader, connection=conn)
        concentration = get_trader_activity_concentration(
            trader,
            connection=conn,
        )
    finally:
        if connection is None:
            conn.close()

    entry_quality = summarize_trader_entry_quality(samples)
    returns = summarize_trader_returns(samples)
    consistency = summarize_trader_consistency(samples)
    recency = summarize_trader_recency(samples, now=now)
    exit_quality = summarize_trader_exit_quality(cycle_data)
    copyability = summarize_trader_copyability(samples)
    evidence = summarize_trader_evidence(
        entry_quality,
        exit_quality,
        concentration,
    )

    scored = calculate_trader_profile_score(
        entry_quality,
        returns,
        exit_quality,
        consistency,
        concentration,
        recency,
        copyability,
    )

    rated = bool(
        evidence["sufficient_evidence"]
        and scored["score"] is not None
    )

    return {
        "trader": trader,
        "observational": True,
        "affects_decisions": False,
        "rated": rated,
        "score": scored["score"] if rated else None,
        "label": (
            None
            if rated
            else TRADER_PROFILE_UNRATED_LABEL
        ),
        "score_components": scored["components"],
        "score_weights": scored["weights_used"],
        "entry_quality": entry_quality,
        "copyability": copyability,
        "returns": returns,
        "consistency": consistency,
        "concentration": concentration,
        "recency": recency,
        "exit_quality": exit_quality,
        "evidence": evidence,
    }


def get_trader_quality_profiles(now=None):
    """Perfil integral de cada trader vigilado."""
    conn = db()

    try:
        return [
            get_trader_quality_profile(
                trader,
                now=now,
                connection=conn,
            )
            for trader in WATCHED.keys()
        ]
    finally:
        conn.close()


def get_signal_first_hit(outcome_id):
    conn = db()

    row = conn.execute(
        """
        SELECT
            signal_ts,
            tp25_ts,
            tp50_ts,
            sl10_ts
        FROM signal_outcomes
        WHERE id = ?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    conn.close()

    if not row:
        return None

    signal_ts = float(row[0] or 0)

    hits = []

    if row[1] is not None:
        hits.append(
            ("TP25", float(row[1]))
        )

    if row[2] is not None:
        hits.append(
            ("TP50", float(row[2]))
        )

    if row[3] is not None:
        hits.append(
            ("SL10", float(row[3]))
        )

    if not hits:
        return None

    hits.sort(
        key=lambda item: item[1]
    )

    first_hit = hits[0][0]
    first_hit_ts = hits[0][1]

    elapsed_seconds = (
        first_hit_ts - signal_ts
    )

    return {
        "first_hit": first_hit,
        "first_hit_ts": first_hit_ts,
        "elapsed_seconds": round(
            elapsed_seconds,
            2
        ),
    }

def get_signal_checkpoint_lags(outcome_id):
    conn = db()

    row = conn.execute(
        """
        SELECT
            signal_ts,
            observed_10s_ts,
            observed_30s_ts,
            observed_1m_ts,
            observed_5m_ts,
            observed_15m_ts
        FROM signal_outcomes
        WHERE id = ?
        LIMIT 1
        """,
        (outcome_id,)
    ).fetchone()

    conn.close()

    if not row:
        return None

    signal_ts = float(row[0] or 0)

    targets = {
        "10s": (row[1], 10),
        "30s": (row[2], 30),
        "1m": (row[3], 60),
        "5m": (row[4], 300),
        "15m": (row[5], 900),
    }

    result = {}

    for name, (observed_ts, target_seconds) in targets.items():

        if observed_ts is None:
            result[name] = None
            continue

        observed_elapsed = (
            float(observed_ts)
            - signal_ts
        )

        lag = (
            observed_elapsed
            - target_seconds
        )

        result[name] = {
            "observed_elapsed": round(
                observed_elapsed,
                2
            ),
            "lag_seconds": round(
                lag,
                2
            ),
        }

    return result

def get_signal_checkpoint_quality(outcome_id):
    lags = get_signal_checkpoint_lags(
        outcome_id
    )

    if lags is None:
        return None

    max_lag_allowed = {
        "10s": 5,
        "30s": 10,
        "1m": 15,
        "5m": 30,
        "15m": 60,
    }

    result = {}

    for checkpoint, data in lags.items():

        if data is None:
            result[checkpoint] = {
                "available": False,
                "reliable": False,
                "lag_seconds": None,
            }
            continue

        lag = float(
            data["lag_seconds"]
        )

        reliable = (
            lag
            <= max_lag_allowed[checkpoint]
        )

        result[checkpoint] = {
            "available": True,
            "reliable": reliable,
            "lag_seconds": round(
                lag,
                2
            ),
        }

    return result


def get_trader_reliable_samples(trader):
    conn = db()

    row = conn.execute(
        """
        SELECT
            SUM(
                CASE
                    WHEN observed_10s_ts IS NOT NULL
                    AND observed_10s_ts - signal_ts <= 15
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN observed_30s_ts IS NOT NULL
                    AND observed_30s_ts - signal_ts <= 40
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN observed_1m_ts IS NOT NULL
                    AND observed_1m_ts - signal_ts <= 75
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN observed_5m_ts IS NOT NULL
                    AND observed_5m_ts - signal_ts <= 330
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN observed_15m_ts IS NOT NULL
                    AND observed_15m_ts - signal_ts <= 960
                    THEN 1
                    ELSE 0
                END
            )

        FROM signal_outcomes

        WHERE trader = ?
        AND price_at_signal > 0
        AND status != 'expired'
        """,
        (trader,)
    ).fetchone()

    conn.close()

    return {
        "trader": trader,
        "reliable_10s": int(row[0] or 0),
        "reliable_30s": int(row[1] or 0),
        "reliable_1m": int(row[2] or 0),
        "reliable_5m": int(row[3] or 0),
        "reliable_15m": int(row[4] or 0),
    }


def get_trader_reliable_returns(trader):
    conn = db()

    row = conn.execute(
        """
        SELECT
            AVG(
                CASE
                    WHEN observed_10s_ts IS NOT NULL
                    AND observed_10s_ts - signal_ts <= 15
                    THEN return_10s
                END
            ),

            AVG(
                CASE
                    WHEN observed_30s_ts IS NOT NULL
                    AND observed_30s_ts - signal_ts <= 40
                    THEN return_30s
                END
            ),

            AVG(
                CASE
                    WHEN observed_1m_ts IS NOT NULL
                    AND observed_1m_ts - signal_ts <= 75
                    THEN return_1m
                END
            ),

            AVG(
                CASE
                    WHEN observed_5m_ts IS NOT NULL
                    AND observed_5m_ts - signal_ts <= 330
                    THEN return_5m
                END
            ),

            AVG(
                CASE
                    WHEN observed_15m_ts IS NOT NULL
                    AND observed_15m_ts - signal_ts <= 960
                    THEN return_15m
                END
            ),

            AVG(
                CASE
                    WHEN observed_5m_ts IS NOT NULL
                    AND observed_5m_ts - signal_ts <= 330
                    AND return_5m IS NOT NULL
                    THEN CASE
                        WHEN return_5m > 0 THEN 1.0
                        ELSE 0.0
                    END
                END
            )

        FROM signal_outcomes

        WHERE trader = ?
        AND price_at_signal > 0
        AND status != 'expired'
        """,
        (trader,)
    ).fetchone()

    conn.close()

    return {
        "trader": trader,
        "avg_return_10s": round(float(row[0] or 0), 2),
        "avg_return_30s": round(float(row[1] or 0), 2),
        "avg_return_1m": round(float(row[2] or 0), 2),
        "avg_return_5m": round(float(row[3] or 0), 2),
        "avg_return_15m": round(float(row[4] or 0), 2),
        "positive_5m_pct": round(
            float(row[5] or 0) * 100,
            2
        ),
    }

    

def get_trader_first_hit_stats(trader):
    conn = db()

    rows = conn.execute(
        """
        SELECT
            tp25_ts,
            sl10_ts
        FROM signal_outcomes
        WHERE trader = ?
        AND price_at_signal > 0
        AND status != 'expired'
        AND (
            tp25_ts IS NOT NULL
            OR sl10_ts IS NOT NULL
        )
        """,
        (trader,)
    ).fetchall()

    conn.close()

    tp25_first = 0
    sl10_first = 0
    only_tp25 = 0
    only_sl10 = 0

    for row in rows:
        tp25_ts = row[0]
        sl10_ts = row[1]

        if tp25_ts is not None and sl10_ts is not None:
            if float(tp25_ts) < float(sl10_ts):
                tp25_first += 1
            elif float(sl10_ts) < float(tp25_ts):
                sl10_first += 1

        elif tp25_ts is not None:
            only_tp25 += 1

        elif sl10_ts is not None:
            only_sl10 += 1

    total = (
        tp25_first
        + sl10_first
        + only_tp25
        + only_sl10
    )

    return {
        "trader": trader,
        "samples": total,
        "tp25_first": tp25_first,
        "sl10_first": sl10_first,
        "only_tp25": only_tp25,
        "only_sl10": only_sl10,
    }


def calculate_copyability_score(trader):
    stats = get_trader_copyability_stats(trader)

    signals = stats["signals"]
    reliable = get_trader_reliable_samples(
    trader
)
    reliable_returns = get_trader_reliable_returns(
    trader
)

    samples_5m = reliable["reliable_5m"]

    if signals == 0:
        return {
            "trader": trader,
            "score": 0,
            "confidence": 0,
            "stats": stats,
        }

    sample_confidence = min(
        samples_5m / 50,
        1.0
    )

    positive_5m = reliable_returns["positive_5m_pct"]
    avg_5m = reliable_returns["avg_return_5m"]

    performance_score = 0.0

    performance_score += min(
        max(positive_5m, 0),
        100
    ) * 0.6

    normalized_return = min(
        max(avg_5m + 50, 0),
        100
    )

    performance_score += (
        normalized_return * 0.4
    )

    final_score = (
        performance_score
        * sample_confidence
    )

    return {
        "trader": trader,
        "score": round(final_score, 2),
        "confidence": round(
            sample_confidence * 100,
            2
        ),
        "stats": stats,
        "reliable_samples": reliable,
        "reliable_returns": reliable_returns,
    }

def load_shadow_model():
    global SHADOW_MODEL
    global SHADOW_MODEL_LAST_ERROR
    global SHADOW_CHALLENGER_MODEL
    global SHADOW_CHALLENGER_LAST_ERROR

    SHADOW_MODEL = None
    SHADOW_MODEL_LAST_ERROR = ""
    SHADOW_CHALLENGER_MODEL = None
    SHADOW_CHALLENGER_LAST_ERROR = ""

    if not SHADOW_MODE_ENABLED:
        return None

    try:
        model = ShadowLogisticModel.from_path(SHADOW_MODEL_PATH)
        if model.data_version != DATA_VERSION:
            raise ValueError(
                "Shadow model data version does not match the app"
            )
        if model.artifact_role == "challenger":
            raise ValueError(
                "A challenger artifact cannot be loaded as incumbent"
            )
        if not model.deployment_ready:
            raise ValueError(
                "An unapproved artifact cannot be loaded as incumbent"
            )
        SHADOW_MODEL = model
        print(
            f"[SHADOW] Loaded {model.model_version} "
            f"from {SHADOW_MODEL_PATH}"
        )
    except Exception as exc:
        SHADOW_MODEL_LAST_ERROR = str(exc)
        print(f"[SHADOW] Disabled: {exc}")

    if SHADOW_CHALLENGER_MODEL_PATH.exists():
        try:
            challenger = ShadowLogisticModel.from_path(
                SHADOW_CHALLENGER_MODEL_PATH
            )
            if challenger.data_version != DATA_VERSION:
                raise ValueError(
                    "Shadow challenger data version does not match the app"
                )
            if challenger.artifact_role != "challenger":
                raise ValueError(
                    "Shadow challenger artifact role is not challenger"
                )
            if (
                SHADOW_MODEL is not None
                and challenger.model_version == SHADOW_MODEL.model_version
            ):
                raise ValueError(
                    "Shadow challenger must have a different model version"
                )
            SHADOW_CHALLENGER_MODEL = challenger
            print(
                f"[SHADOW] Loaded challenger "
                f"{challenger.model_version} "
                f"from {SHADOW_CHALLENGER_MODEL_PATH}"
            )
        except Exception as exc:
            SHADOW_CHALLENGER_LAST_ERROR = str(exc)
            print(f"[SHADOW] Challenger disabled: {exc}")

    return SHADOW_MODEL


def build_model_features(
    trader,
    mint,
    signal_ts,
    trader_score,
    timing_score,
    size_score,
    token_score,
    consensus_score,
    market_score,
    score_total,
    market_cap,
    sol_amount,
    price_at_signal,
    connection=None,
):
    buy_size_pct_mc = (
        (float(sol_amount) / float(market_cap)) * 100
        if float(market_cap or 0) > 0
        else 0.0
    )
    signal_ts = float(signal_ts)
    owns_connection = connection is None
    conn = connection if connection is not None else db()
    try:
        create_ts = conn.execute(
            """
            SELECT MIN(ts)
            FROM trades
            WHERE mint = ?
            AND side = 'create'
            AND ts <= ?
            """,
            (mint, signal_ts),
        ).fetchone()[0]
        previous_buy_ts = conn.execute(
            """
            SELECT MAX(ts)
            FROM trades
            WHERE trader = ?
            AND ts < ?
            AND side LIKE '%buy%'
            """,
            (trader, signal_ts),
        ).fetchone()[0]
        recent_buy_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM trades
            WHERE trader = ?
            AND ts >= ?
            AND ts < ?
            AND side LIKE '%buy%'
            """,
            (trader, signal_ts - 60, signal_ts),
        ).fetchone()[0]
        consensus_30s = conn.execute(
            """
            SELECT COUNT(DISTINCT trader)
            FROM trades
            WHERE mint = ?
            AND ts >= ?
            AND ts <= ?
            AND (side LIKE '%buy%' OR side = 'create')
            """,
            (mint, signal_ts - 30, signal_ts),
        ).fetchone()[0]
        consensus_window = conn.execute(
            """
            SELECT COUNT(DISTINCT trader)
            FROM trades
            WHERE mint = ?
            AND ts >= ?
            AND ts <= ?
            AND (side LIKE '%buy%' OR side = 'create')
            """,
            (mint, signal_ts - WINDOW, signal_ts),
        ).fetchone()[0]
    finally:
        if owns_connection:
            conn.close()

    token_age_seconds = (
        signal_ts - float(create_ts)
        if create_ts is not None and signal_ts >= float(create_ts)
        else None
    )
    previous_gap = (
        signal_ts - float(previous_buy_ts)
        if (
            previous_buy_ts is not None
            and signal_ts >= float(previous_buy_ts)
        )
        else None
    )

    return {
        "trader": str(trader or "unknown"),
        "trader_score": int(trader_score or 0),
        "timing_score": int(timing_score or 0),
        "size_score": int(size_score or 0),
        "token_score": int(token_score or 0),
        "consensus_score": int(consensus_score or 0),
        "market_score": int(market_score or 0),
        "score_total": int(score_total or 0),
        "market_cap": float(market_cap or 0),
        "sol_amount": float(sol_amount or 0),
        "price_at_signal": float(price_at_signal or 0),
        "buy_size_pct_mc": round(buy_size_pct_mc, 4),
        "token_age_seconds": (
            round(token_age_seconds, 3)
            if token_age_seconds is not None
            else None
        ),
        "trader_previous_buy_gap_seconds": (
            round(previous_gap, 3)
            if previous_gap is not None
            else None
        ),
        "trader_recent_buy_count_60s": int(recent_buy_count or 0),
        "consensus_trader_count_30s": int(consensus_30s or 0),
        "consensus_trader_count": int(consensus_window or 0),
    }


def record_shadow_prediction(evaluation_id, features):
    global SHADOW_MODEL_LAST_ERROR
    global SHADOW_CHALLENGER_LAST_ERROR

    if not SHADOW_MODE_ENABLED or SHADOW_MODEL is None:
        return None

    incumbent_prediction = None
    models = (
        ("incumbent", SHADOW_MODEL),
        ("challenger", SHADOW_CHALLENGER_MODEL),
    )

    for role, model in models:
        if model is None:
            continue

        conn = None
        try:
            prediction = model.predict(features)
            conn = db()
            conn.execute(
                """
                INSERT OR IGNORE INTO model_shadow_predictions(
                    evaluation_id,
                    created_ts,
                    model_version,
                    data_version,
                    probability,
                    threshold,
                    predicted_target,
                    features_json
                )
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    int(evaluation_id),
                    time.time(),
                    prediction["model_version"],
                    prediction["data_version"],
                    prediction["probability"],
                    prediction["threshold"],
                    prediction["predicted_target"],
                    json.dumps(features, sort_keys=True),
                ),
            )
            conn.commit()
            if role == "incumbent":
                SHADOW_MODEL_LAST_ERROR = ""
                incumbent_prediction = prediction
            else:
                SHADOW_CHALLENGER_LAST_ERROR = ""
        except Exception as exc:
            if role == "incumbent":
                SHADOW_MODEL_LAST_ERROR = str(exc)
            else:
                SHADOW_CHALLENGER_LAST_ERROR = str(exc)
            print(f"[SHADOW] {role.title()} prediction failed: {exc}")
        finally:
            if conn is not None:
                conn.close()

    return incumbent_prediction


def observe_shadow_signal(
    evaluation_id,
    connection=None,
    **feature_values,
):
    global SHADOW_MODEL_LAST_ERROR

    if not SHADOW_MODE_ENABLED or SHADOW_MODEL is None:
        return None

    try:
        features = build_model_features(
            connection=connection,
            **feature_values,
        )
        return record_shadow_prediction(evaluation_id, features)
    except Exception as exc:
        SHADOW_MODEL_LAST_ERROR = str(exc)
        print(f"[SHADOW] Feature collection failed: {exc}")
        return None


def get_shadow_predictions(limit=100, before_id=0):
    safe_before_id = max(0, int(before_id or 0))
    conn = db()
    rows = conn.execute(
        """
        SELECT
            p.id,
            p.evaluation_id,
            p.created_ts,
            p.model_version,
            p.probability,
            p.threshold,
            p.predicted_target,
            e.trader,
            e.mint,
            e.decision,
            o.status,
            CASE
                WHEN o.status = 'completed' THEN
                    CASE
                        WHEN o.tp25_ts IS NOT NULL
                        AND (
                            o.sl10_ts IS NULL
                            OR o.tp25_ts < o.sl10_ts
                        )
                        THEN 1
                        ELSE 0
                    END
                ELSE NULL
            END AS actual_target
        FROM model_shadow_predictions p
        JOIN evaluations e ON e.id = p.evaluation_id
        LEFT JOIN signal_outcomes o ON o.signal_id = p.evaluation_id
        WHERE (? = 0 OR p.id < ?)
        ORDER BY p.id DESC
        LIMIT ?
        """,
        (
            safe_before_id,
            safe_before_id,
            max(1, min(int(limit or 100), 1000)),
        ),
    ).fetchall()
    conn.close()

    return [
        {
            "prediction_id": int(row[0]),
            "evaluation_id": int(row[1]),
            "created_ts": float(row[2]),
            "model_version": str(row[3]),
            "probability": round(float(row[4]), 6),
            "threshold": float(row[5]),
            "predicted_target": int(row[6]),
            "trader": str(row[7] or "unknown"),
            "mint": str(row[8] or ""),
            "agent_decision": str(row[9] or ""),
            "outcome_status": str(row[10] or "missing"),
            "actual_target": (
                int(row[11]) if row[11] is not None else None
            ),
        }
        for row in rows
    ]


def summarize_shadow_predictions(rows):
    completed = [
        row for row in rows if row["actual_target"] is not None
    ]
    true_positive = sum(
        row["predicted_target"] == 1 and row["actual_target"] == 1
        for row in completed
    )
    false_positive = sum(
        row["predicted_target"] == 1 and row["actual_target"] == 0
        for row in completed
    )
    true_negative = sum(
        row["predicted_target"] == 0 and row["actual_target"] == 0
        for row in completed
    )
    false_negative = sum(
        row["predicted_target"] == 0 and row["actual_target"] == 1
        for row in completed
    )
    predicted_positive = true_positive + false_positive
    actual_positive = true_positive + false_negative

    return {
        "total": len(rows),
        "pending": len(rows) - len(completed),
        "completed": len(completed),
        "precision": (
            round(true_positive / predicted_positive, 6)
            if predicted_positive
            else None
        ),
        "recall": (
            round(true_positive / actual_positive, 6)
            if actual_positive
            else None
        ),
        "confusion_matrix": [
            [true_negative, false_positive],
            [false_negative, true_positive],
        ],
    }


def compare_shadow_models(
    rows_by_version,
    incumbent_version,
    challenger_version,
):
    if not incumbent_version or not challenger_version:
        return None

    incumbent_by_evaluation = {
        row["evaluation_id"]: row
        for row in rows_by_version.get(incumbent_version, [])
    }
    challenger_by_evaluation = {
        row["evaluation_id"]: row
        for row in rows_by_version.get(challenger_version, [])
    }
    paired_ids = sorted(
        set(incumbent_by_evaluation)
        & set(challenger_by_evaluation)
    )
    incumbent_rows = [
        incumbent_by_evaluation[evaluation_id]
        for evaluation_id in paired_ids
    ]
    challenger_rows = [
        challenger_by_evaluation[evaluation_id]
        for evaluation_id in paired_ids
    ]
    completed_pairs = [
        (incumbent, challenger)
        for incumbent, challenger in zip(
            incumbent_rows,
            challenger_rows,
        )
        if incumbent["actual_target"] is not None
    ]
    agreements = sum(
        incumbent["predicted_target"] == challenger["predicted_target"]
        for incumbent, challenger in zip(
            incumbent_rows,
            challenger_rows,
        )
    )
    incumbent_metrics = summarize_shadow_predictions(incumbent_rows)
    challenger_metrics = summarize_shadow_predictions(challenger_rows)

    def metric_delta(name):
        incumbent_value = incumbent_metrics[name]
        challenger_value = challenger_metrics[name]
        if incumbent_value is None or challenger_value is None:
            return None
        return round(challenger_value - incumbent_value, 6)

    return {
        "incumbent_version": incumbent_version,
        "challenger_version": challenger_version,
        "total": len(paired_ids),
        "pending": len(paired_ids) - len(completed_pairs),
        "completed": len(completed_pairs),
        "agreement_rate": (
            round(agreements / len(paired_ids), 6)
            if paired_ids
            else None
        ),
        "both_positive": sum(
            incumbent["predicted_target"] == 1
            and challenger["predicted_target"] == 1
            for incumbent, challenger in zip(
                incumbent_rows,
                challenger_rows,
            )
        ),
        "both_negative": sum(
            incumbent["predicted_target"] == 0
            and challenger["predicted_target"] == 0
            for incumbent, challenger in zip(
                incumbent_rows,
                challenger_rows,
            )
        ),
        "incumbent_only_positive": sum(
            incumbent["predicted_target"] == 1
            and challenger["predicted_target"] == 0
            for incumbent, challenger in zip(
                incumbent_rows,
                challenger_rows,
            )
        ),
        "challenger_only_positive": sum(
            incumbent["predicted_target"] == 0
            and challenger["predicted_target"] == 1
            for incumbent, challenger in zip(
                incumbent_rows,
                challenger_rows,
            )
        ),
        "incumbent_only_correct": sum(
            incumbent["predicted_target"] == incumbent["actual_target"]
            and challenger["predicted_target"] != challenger["actual_target"]
            for incumbent, challenger in completed_pairs
        ),
        "challenger_only_correct": sum(
            challenger["predicted_target"] == challenger["actual_target"]
            and incumbent["predicted_target"] != incumbent["actual_target"]
            for incumbent, challenger in completed_pairs
        ),
        "both_correct": sum(
            incumbent["predicted_target"] == incumbent["actual_target"]
            and challenger["predicted_target"] == challenger["actual_target"]
            for incumbent, challenger in completed_pairs
        ),
        "both_wrong": sum(
            incumbent["predicted_target"] != incumbent["actual_target"]
            and challenger["predicted_target"] != challenger["actual_target"]
            for incumbent, challenger in completed_pairs
        ),
        "incumbent_metrics": incumbent_metrics,
        "challenger_metrics": challenger_metrics,
        "precision_delta": metric_delta("precision"),
        "recall_delta": metric_delta("recall"),
    }


def assess_shadow_challenger(comparison):
    completed = int((comparison or {}).get("completed") or 0)
    precision_delta = (comparison or {}).get("precision_delta")
    recall_delta = (comparison or {}).get("recall_delta")
    ready = (
        completed >= SHADOW_REVIEW_MIN_COMPLETED
        and precision_delta is not None
        and recall_delta is not None
    )

    leader = None
    if (
        ready
        and precision_delta is not None
        and recall_delta is not None
    ):
        if (
            precision_delta >= 0
            and recall_delta >= 0
            and (precision_delta > 0 or recall_delta > 0)
        ):
            leader = "challenger"
        elif (
            precision_delta <= 0
            and recall_delta <= 0
            and (precision_delta < 0 or recall_delta < 0)
        ):
            leader = "incumbent"
        else:
            leader = "mixed"

    blockers = []
    if completed < SHADOW_REVIEW_MIN_COMPLETED:
        blockers.append(
            f"paired_completed "
            f"{completed}/{SHADOW_REVIEW_MIN_COMPLETED}"
        )
    elif precision_delta is None or recall_delta is None:
        blockers.append("paired_metrics_unavailable")

    return {
        "minimum_completed": SHADOW_REVIEW_MIN_COMPLETED,
        "ready_for_review": ready,
        "leader": leader,
        "blockers": blockers,
    }


def get_shadow_stats():
    conn = db()
    raw_rows = conn.execute(
        """
        SELECT
            p.evaluation_id,
            p.model_version,
            p.predicted_target,
            CASE
                WHEN o.status = 'completed' THEN
                    CASE
                        WHEN o.tp25_ts IS NOT NULL
                        AND (
                            o.sl10_ts IS NULL
                            OR o.tp25_ts < o.sl10_ts
                        )
                        THEN 1
                        ELSE 0
                    END
                ELSE NULL
            END AS actual_target
        FROM model_shadow_predictions p
        LEFT JOIN signal_outcomes o ON o.signal_id = p.evaluation_id
        ORDER BY p.id ASC
        """
    ).fetchall()
    conn.close()

    current_version = getattr(SHADOW_MODEL, "model_version", None)
    challenger_version = getattr(
        SHADOW_CHALLENGER_MODEL,
        "model_version",
        None,
    )
    rows_by_version = {}
    for (
        evaluation_id,
        model_version,
        predicted_target,
        actual_target,
    ) in raw_rows:
        model_version = str(model_version)
        rows_by_version.setdefault(model_version, []).append({
            "evaluation_id": int(evaluation_id),
            "predicted_target": int(predicted_target),
            "actual_target": (
                int(actual_target) if actual_target is not None else None
            ),
        })

    models = {
        version: summarize_shadow_predictions(rows)
        for version, rows in rows_by_version.items()
    }
    aggregate_rows = [
        row
        for rows in rows_by_version.values()
        for row in rows
    ]
    current_metrics = models.get(
        current_version,
        summarize_shadow_predictions(aggregate_rows),
    )

    comparison = compare_shadow_models(
        rows_by_version,
        current_version,
        challenger_version,
    )

    return {
        "enabled": bool(SHADOW_MODE_ENABLED),
        "model_loaded": SHADOW_MODEL is not None,
        "model_version": current_version,
        "challenger_model_loaded": SHADOW_CHALLENGER_MODEL is not None,
        "challenger_model_version": challenger_version,
        "challenger_model_last_error": (
            SHADOW_CHALLENGER_LAST_ERROR or None
        ),
        "all_versions_total": len(raw_rows),
        "version_counts": {
            version: metrics["total"]
            for version, metrics in models.items()
        },
        "models": models,
        "comparison": comparison,
        "promotion_assessment": assess_shadow_challenger(comparison),
        **current_metrics,
    }


def get_latest_helius_pump_event_received_ts(connection=None):
    owns_connection = connection is None
    conn = connection or db()
    try:
        row = conn.execute(
            "SELECT received_ts FROM helius_webhook_events "
            "WHERE parsed = 1 ORDER BY received_ts DESC LIMIT 1"
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    finally:
        if owns_connection:
            conn.close()


def get_live_exit_feed_readiness(now=None):
    status = get_helius_webhook_sync_status()
    blockers = []
    if not MARKET_EVENT_INBOX_CONSUMER_ENABLED:
        blockers.append("HELIUS_INBOX_CONSUMER_DISABLED")
    if not status["enabled"] or not status["apply"]:
        blockers.append("HELIUS_TOKEN_SYNC_NOT_APPLIED")
    if not status["configured"] or not status["initialized"]:
        blockers.append("HELIUS_TOKEN_SYNC_NOT_READY")
    if status["last_error"]:
        blockers.append("HELIUS_TOKEN_SYNC_UNHEALTHY")
    if status["pending_tokens"] or status["planned_additions"]:
        blockers.append("HELIUS_TOKEN_SYNC_PENDING")

    current_ts = float(now if now is not None else time.time())
    last_success_ts = status["last_success_ts"]
    maximum_age = max(600, HELIUS_WEBHOOK_SYNC_AUDIT_SECONDS * 2)
    if (
        last_success_ts is None
        or current_ts - float(last_success_ts) > maximum_age
    ):
        blockers.append("HELIUS_TOKEN_SYNC_STALE")
    maximum_event_age = 1800
    try:
        last_event_ts = get_latest_helius_pump_event_received_ts()
    except Exception:
        last_event_ts = None
    if (
        last_event_ts is None
        or not math.isfinite(last_event_ts)
        or current_ts - last_event_ts > maximum_event_age
    ):
        blockers.append("HELIUS_PUMP_WEBHOOK_STALE")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "last_success_ts": last_success_ts,
        "maximum_age_seconds": maximum_age,
        "last_pump_event_received_ts": last_event_ts,
        "maximum_pump_event_age_seconds": maximum_event_age,
    }


def get_live_canary_blockers(trader=None, amount_usd=None):
    blockers = []
    if not LIVE_CANARY_ENABLED:
        blockers.append("LIVE_CANARY_DISABLED")

    loaded_model_version = str(
        getattr(SHADOW_MODEL, "model_version", "") or ""
    )
    if not LIVE_APPROVED_MODEL_VERSION:
        blockers.append("LIVE_APPROVED_MODEL_VERSION_MISSING")
    elif loaded_model_version != LIVE_APPROVED_MODEL_VERSION:
        blockers.append("LIVE_APPROVED_MODEL_VERSION_MISMATCH")
    if not bool(getattr(SHADOW_MODEL, "deployment_ready", False)):
        blockers.append("LIVE_MODEL_NOT_DEPLOYMENT_READY")
    if not LIVE_CANARY_REVIEWED_MODEL_VERSION:
        blockers.append("LIVE_MODEL_ECONOMICS_REVIEW_REQUIRED")
    elif loaded_model_version != LIVE_CANARY_REVIEWED_MODEL_VERSION:
        blockers.append("LIVE_MODEL_ECONOMICS_VERSION_MISMATCH")

    max_buy_usd_valid = (
        math.isfinite(LIVE_CANARY_MAX_BUY_USD)
        and 0 < LIVE_CANARY_MAX_BUY_USD <= LIVE_CANARY_HARD_MAX_BUY_USD
    )
    if not max_buy_usd_valid:
        blockers.append("LIVE_CANARY_MAX_BUY_USD_INVALID")
    max_buys_per_day_valid = (
        0 < LIVE_CANARY_MAX_BUYS_PER_DAY
        <= LIVE_CANARY_HARD_MAX_BUYS_PER_DAY
    )
    if not max_buys_per_day_valid:
        blockers.append("LIVE_CANARY_MAX_BUYS_PER_DAY_INVALID")
    max_daily_notional_valid = (
        math.isfinite(LIVE_CANARY_MAX_DAILY_NOTIONAL_USD)
        and 0 < LIVE_CANARY_MAX_DAILY_NOTIONAL_USD
        <= LIVE_CANARY_HARD_MAX_DAILY_NOTIONAL_USD
    )
    if not max_daily_notional_valid:
        blockers.append("LIVE_CANARY_MAX_DAILY_NOTIONAL_USD_INVALID")

    try:
        selected_amount = float(
            LIVE_BUY_USD if amount_usd is None else amount_usd
        )
    except (TypeError, ValueError):
        selected_amount = 0.0
    if (
        not max_buy_usd_valid
        or not math.isfinite(selected_amount)
        or selected_amount <= 0
        or selected_amount > LIVE_CANARY_MAX_BUY_USD
        or selected_amount > LIVE_CANARY_HARD_MAX_BUY_USD
    ):
        blockers.append("LIVE_CANARY_BUY_AMOUNT_INVALID")

    if not LIVE_CANARY_ALLOWED_TRADERS:
        blockers.append("LIVE_CANARY_TRADERS_MISSING")
    elif trader is not None and trader not in LIVE_CANARY_ALLOWED_TRADERS:
        blockers.append("LIVE_CANARY_TRADER_NOT_ALLOWED")

    if not LIVE_SELLS_ENABLED:
        blockers.append("LIVE_SELLS_REQUIRED_FOR_BUYS")
    try:
        exit_feed = get_live_exit_feed_readiness()
    except Exception:
        blockers.append("LIVE_EXIT_FEED_STATUS_UNKNOWN")
    else:
        if not exit_feed["ready"]:
            blockers.append("LIVE_EXIT_FEED_NOT_READY")
            blockers.extend(exit_feed.get("blockers") or [])

    try:
        exposure = get_daily_live_buy_exposure(trader=trader)
    except Exception:
        blockers.append("LIVE_CANARY_EXPOSURE_UNKNOWN")
    else:
        if int(exposure.get("unresolved_without_signature") or 0):
            blockers.append("LIVE_AMBIGUOUS_ORDER_PENDING")
        if (
            max_buys_per_day_valid
            and exposure["attempts"] >= LIVE_CANARY_MAX_BUYS_PER_DAY
        ):
            blockers.append("LIVE_CANARY_BUY_LIMIT_REACHED")
        if (
            trader
            and int(exposure.get("trader_attempts") or 0)
            >= LIVE_CANARY_HARD_MAX_BUYS_PER_TRADER_PER_DAY
        ):
            blockers.append("LIVE_CANARY_TRADER_BUY_LIMIT_REACHED")
        if (
            max_daily_notional_valid
            and exposure["notional_usd"] + max(selected_amount, 0)
            > LIVE_CANARY_MAX_DAILY_NOTIONAL_USD
        ):
            blockers.append("LIVE_CANARY_NOTIONAL_LIMIT_REACHED")
    return blockers


def get_live_execution_readiness(
    execution_side=None,
    trader=None,
    amount_usd=None,
):
    execution_side = str(
        execution_side or ""
    ).strip().lower()

    shadow_stats = get_shadow_stats()
    assessment = shadow_stats["promotion_assessment"]
    blockers = []
    canary_blockers = []

    if not API_KEY:
        blockers.append("PUMPPORTAL_API_KEY_MISSING")
    if not PUMPPORTAL_TRADING_WALLET_ADDRESS:
        blockers.append("PUMPPORTAL_TRADING_WALLET_MISSING")
    elif (
        not PUMPPORTAL_WALLET_ADDRESS
        or PUMPPORTAL_TRADING_WALLET_ADDRESS != PUMPPORTAL_WALLET_ADDRESS
    ):
        blockers.append("PUMPPORTAL_WALLET_MISMATCH")
    if not STREAM_CONNECTED:
        blockers.append("STREAM_DISCONNECTED")
    if PUMPPORTAL_WALLET_BALANCE_SOL is None:
        blockers.append("PUMPPORTAL_BALANCE_UNKNOWN")
    elif PUMPPORTAL_WALLET_BALANCE_SOL < PUMPPORTAL_LOW_BALANCE_SOL:
        blockers.append("PUMPPORTAL_BALANCE_LOW")
    if KILL_SWITCH:
        blockers.append("KILL_SWITCH_ACTIVE")
    if not LIVE_EXECUTION_IMPLEMENTED:
        blockers.append("LIVE_EXECUTION_NOT_IMPLEMENTED")
    if not LIVE_TRADING:
        blockers.append("LIVE_TRADING_DISABLED")
    if (
        not execution_side
        and LIVE_TRADING
        and LIVE_EXECUTION_IMPLEMENTED
        and not (
            LIVE_BUYS_ENABLED
            or LIVE_SELLS_ENABLED
        )
    ):
        blockers.append("LIVE_BUYS_AND_SELLS_DISABLED")
    if execution_side == "buy" and not LIVE_BUYS_ENABLED:
        blockers.append("LIVE_BUYS_DISABLED")
    if (
        execution_side == "buy"
        and LIVE_BUYS_ENABLED
        and (
            not math.isfinite(LIVE_BUY_USD)
            or LIVE_BUY_USD <= 0
            or LIVE_BUY_USD > MAX_POSITION_USD
        )
    ):
        blockers.append("LIVE_BUY_USD_INVALID")
    if execution_side == "sell" and not LIVE_SELLS_ENABLED:
        blockers.append("LIVE_SELLS_DISABLED")
    if execution_side == "buy" or (
        not execution_side and LIVE_BUYS_ENABLED
    ):
        canary_blockers = get_live_canary_blockers(trader, amount_usd)
        blockers.extend(canary_blockers)

    common_execution_ready = bool(
        API_KEY
        and PUMPPORTAL_TRADING_WALLET_ADDRESS
        and PUMPPORTAL_TRADING_WALLET_ADDRESS == PUMPPORTAL_WALLET_ADDRESS
        and STREAM_CONNECTED
        and PUMPPORTAL_WALLET_BALANCE_SOL is not None
        and PUMPPORTAL_WALLET_BALANCE_SOL >= PUMPPORTAL_LOW_BALANCE_SOL
        and not KILL_SWITCH
        and LIVE_EXECUTION_IMPLEMENTED
        and LIVE_TRADING
    )

    return {
        "ready": not blockers,
        "blockers": blockers,
        "execution_side": execution_side or None,
        "shadow_review": assessment,
        "stream_connected": bool(STREAM_CONNECTED),
        "pumpportal_wallet_balance_sol": PUMPPORTAL_WALLET_BALANCE_SOL,
        "pumpportal_low_balance_threshold_sol": PUMPPORTAL_LOW_BALANCE_SOL,
        "pumpportal_wallet_matches": bool(
            PUMPPORTAL_TRADING_WALLET_ADDRESS
            and PUMPPORTAL_TRADING_WALLET_ADDRESS
            == PUMPPORTAL_WALLET_ADDRESS
        ),
        "kill_switch": bool(KILL_SWITCH),
        "live_execution_implemented": bool(LIVE_EXECUTION_IMPLEMENTED),
        "live_trading_enabled": bool(LIVE_TRADING),
        "live_buys_enabled": bool(LIVE_BUYS_ENABLED),
        "live_sells_enabled": bool(LIVE_SELLS_ENABLED),
        "live_buy_usd": LIVE_BUY_USD,
        "live_canary_enabled": bool(LIVE_CANARY_ENABLED),
        "live_approved_model_version": LIVE_APPROVED_MODEL_VERSION or None,
        "live_reviewed_model_version": (
            LIVE_CANARY_REVIEWED_MODEL_VERSION or None
        ),
        "live_canary_max_buy_usd": LIVE_CANARY_MAX_BUY_USD,
        "live_canary_max_buys_per_day": LIVE_CANARY_MAX_BUYS_PER_DAY,
        "live_canary_max_buys_per_trader_per_day": (
            LIVE_CANARY_HARD_MAX_BUYS_PER_TRADER_PER_DAY
        ),
        "live_canary_max_daily_notional_usd": (
            LIVE_CANARY_MAX_DAILY_NOTIONAL_USD
        ),
        "live_canary_allowed_traders": sorted(
            LIVE_CANARY_ALLOWED_TRADERS
        ),
        "live_buy_ready": bool(
            common_execution_ready
            and LIVE_BUYS_ENABLED
            and math.isfinite(LIVE_BUY_USD)
            and 0 < LIVE_BUY_USD <= MAX_POSITION_USD
            and not canary_blockers
        ),
        "live_sell_ready": bool(
            common_execution_ready
            and LIVE_SELLS_ENABLED
        ),
    }


def get_training_dataset_rows():
    conn = db()

    rows = conn.execute(
        """
        SELECT
            e.id,
            e.trader,
            e.ts,
            e.mint,
            e.trader_score,
            e.timing_score,
            e.size_score,
            e.token_score,
            e.consensus_score,
            e.market_score,
            e.score,
            e.market_cap,
            e.sol_amount,

            o.price_at_signal,
            o.tp25_ts,
            o.sl10_ts,
            o.status

        FROM evaluations e

        JOIN signal_outcomes o
            ON o.signal_id = e.id

        WHERE o.price_at_signal > 0
        AND o.status = 'completed'
        AND e.market_cap > 0
        AND e.sol_amount > 0
        AND e.data_version = ?

        ORDER BY e.ts ASC
        """,
        (DATA_VERSION,)
        ).fetchall()

    conn.close()

    dataset = []

    for row in rows:
        signal_id = int(row[0])
        trader = str(row[1] or "unknown")
        signal_ts = float(row[2] or 0)
        mint = str(row[3] or "")

        trader_score = int(row[4] or 0)
        timing_score = int(row[5] or 0)
        size_score = int(row[6] or 0)
        token_score = int(row[7] or 0)
        consensus_score = int(row[8] or 0)
        market_score = int(row[9] or 0)

        score_total = int(row[10] or 0)
        market_cap = float(row[11] or 0)
        sol_amount = float(row[12] or 0)

        price_at_signal = float(row[13] or 0)
        tp25_ts = row[14]
        sl10_ts = row[15]

        features = build_model_features(
            trader=trader,
            mint=mint,
            signal_ts=signal_ts,
            trader_score=trader_score,
            timing_score=timing_score,
            size_score=size_score,
            token_score=token_score,
            consensus_score=consensus_score,
            market_score=market_score,
            score_total=score_total,
            market_cap=market_cap,
            sol_amount=sol_amount,
            price_at_signal=price_at_signal,
        )

        target = 0

        if tp25_ts is not None:
            if (
                sl10_ts is None
                or float(tp25_ts) < float(sl10_ts)
            ):
                target = 1

        dataset.append(
            {
                "signal_id": signal_id,
                "signal_ts": signal_ts,
                "mint": mint,
                **features,
                "target_tp25_before_sl10": target,
            }
        )

    return dataset

def get_training_dataset_stats():
    conn = db()

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,

            SUM(
                CASE
                    WHEN o.status = 'active'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'completed'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'expired'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'completed'
                    AND o.tp25_ts IS NOT NULL
                    AND (
                        o.sl10_ts IS NULL
                        OR o.tp25_ts < o.sl10_ts
                    )
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'completed'
                    AND (
                        o.tp25_ts IS NULL
                        OR (
                            o.sl10_ts IS NOT NULL
                            AND o.sl10_ts <= o.tp25_ts
                        )
                    )
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.price_10s IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.price_30s IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.price_1m IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.price_5m IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.price_15m IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            )

        FROM evaluations e

        JOIN signal_outcomes o
            ON o.signal_id = e.id

        WHERE e.data_version = ?
        AND e.market_cap > 0
        AND e.sol_amount > 0
        AND o.price_at_signal > 0
        """,
        (DATA_VERSION,)
    ).fetchone()

    conn.close()

    return {
        "data_version": DATA_VERSION,

        "total": int(row[0] or 0),
        "active": int(row[1] or 0),
        "completed": int(row[2] or 0),
        "expired": int(row[3] or 0),

        "target_1": int(row[4] or 0),
        "target_0": int(row[5] or 0),

        "checkpoint_10s": int(row[6] or 0),
        "checkpoint_30s": int(row[7] or 0),
        "checkpoint_1m": int(row[8] or 0),
        "checkpoint_5m": int(row[9] or 0),
        "checkpoint_15m": int(row[10] or 0),
    }


def get_training_stats_by_trader():
    conn = db()

    rows = conn.execute(
        """
        SELECT
            e.trader,

            COUNT(*) AS total,

            SUM(
                CASE
                    WHEN o.status = 'active'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'completed'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'expired'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'completed'
                    AND o.tp25_ts IS NOT NULL
                    AND (
                        o.sl10_ts IS NULL
                        OR o.tp25_ts < o.sl10_ts
                    )
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN o.status = 'completed'
                    AND (
                        o.tp25_ts IS NULL
                        OR (
                            o.sl10_ts IS NOT NULL
                            AND o.sl10_ts <= o.tp25_ts
                        )
                    )
                    THEN 1
                    ELSE 0
                END
            )

        FROM evaluations e

        JOIN signal_outcomes o
            ON o.signal_id = e.id

        WHERE e.data_version = ?
        AND e.market_cap > 0
        AND e.sol_amount > 0
        AND o.price_at_signal > 0

        GROUP BY e.trader
        ORDER BY total DESC
        """,
        (DATA_VERSION,)
    ).fetchall()

    conn.close()

    return {
        "data_version": DATA_VERSION,
        "traders": [
            {
                "trader": str(row[0] or "unknown"),
                "total": int(row[1] or 0),
                "active": int(row[2] or 0),
                "completed": int(row[3] or 0),
                "expired": int(row[4] or 0),
                "target_1": int(row[5] or 0),
                "target_0": int(row[6] or 0),
            }
            for row in rows
        ],
    }


def get_training_expired_preview(
    limit=20
):
    conn = db()

    rows = conn.execute(
        """
        SELECT
            o.id,
            o.signal_id,
            o.mint,
            o.trader,
            o.signal_ts,
            o.price_10s,
            o.price_30s,
            o.price_1m,
            o.price_5m,
            o.price_15m,
            o.observed_10s_ts,
            o.observed_30s_ts,
            o.observed_1m_ts,
            o.observed_5m_ts,
            o.observed_15m_ts

        FROM signal_outcomes o

        JOIN evaluations e
            ON e.id = o.signal_id

        WHERE e.data_version = ?
        AND o.status = 'expired'
        AND e.market_cap > 0
        AND e.sol_amount > 0
        AND o.price_at_signal > 0

        ORDER BY o.id DESC
        LIMIT ?
        """,
        (
            DATA_VERSION,
            int(limit or 20),
        )
    ).fetchall()

    conn.close()

    return {
        "data_version": DATA_VERSION,
        "count": len(rows),
        "rows": [
            {
                "id": int(row[0] or 0),
                "signal_id": int(row[1] or 0),
                "mint": str(row[2] or ""),
                "trader": str(row[3] or "unknown"),
                "signal_ts": float(row[4] or 0),
                "has_10s": row[5] is not None,
                "has_30s": row[6] is not None,
                "has_1m": row[7] is not None,
                "has_5m": row[8] is not None,
                "has_15m": row[9] is not None,
                "observed_10s_ts": row[10],
                "observed_30s_ts": row[11],
                "observed_1m_ts": row[12],
                "observed_5m_ts": row[13],
                "observed_15m_ts": row[14],
            }
            for row in rows
        ],
    }


def get_training_checkpoint_freshness():
    conn = db()

    rows = conn.execute(
        """
        SELECT
            o.id,
            o.signal_id,
            o.mint,
            o.trader,

            o.observed_10s_ts - (
                SELECT MAX(h.ts)
                FROM token_history h
                WHERE h.mint = o.mint
                AND h.ts >= o.signal_ts
                AND h.ts <= o.observed_10s_ts
            ),

            o.observed_30s_ts - (
                SELECT MAX(h.ts)
                FROM token_history h
                WHERE h.mint = o.mint
                AND h.ts >= o.signal_ts
                AND h.ts <= o.observed_30s_ts
            ),

            o.observed_1m_ts - (
                SELECT MAX(h.ts)
                FROM token_history h
                WHERE h.mint = o.mint
                AND h.ts >= o.signal_ts
                AND h.ts <= o.observed_1m_ts
            ),

            o.observed_5m_ts - (
                SELECT MAX(h.ts)
                FROM token_history h
                WHERE h.mint = o.mint
                AND h.ts >= o.signal_ts
                AND h.ts <= o.observed_5m_ts
            ),

            o.observed_15m_ts - (
                SELECT MAX(h.ts)
                FROM token_history h
                WHERE h.mint = o.mint
                AND h.ts >= o.signal_ts
                AND h.ts <= o.observed_15m_ts
            )

        FROM signal_outcomes o

        JOIN evaluations e
            ON e.id = o.signal_id

        WHERE e.data_version = ?
        AND o.status = 'completed'

        ORDER BY o.id DESC
        """,
        (DATA_VERSION,)
    ).fetchall()

    conn.close()

    result = []

    for row in rows:
        ages = [
            (
                round(float(value), 3)
                if value is not None
                else None
            )
            for value in row[4:9]
        ]

        valid_ages = [
            value
            for value in ages
            if value is not None
        ]

        max_age = (
            max(valid_ages)
            if valid_ages
            else None
        )

        source_older_than_5s = (
            len(valid_ages) != 5
            or (
                max_age is not None
                and max_age > 5
            )
        )

        result.append(
            {
                "id": int(row[0]),
                "signal_id": int(row[1]),
                "mint": str(row[2] or ""),
                "trader": str(row[3] or "unknown"),
                "age_10s": ages[0],
                "age_30s": ages[1],
                "age_1m": ages[2],
                "age_5m": ages[3],
                "age_15m": ages[4],
                "max_age_seconds": max_age,
                "source_older_than_5s": source_older_than_5s,
            }
        )

    return {
        "data_version": DATA_VERSION,
        "completed": len(result),
        "source_older_than_5s_count": sum(
            1 for item in result if item["source_older_than_5s"]
        ),
        "rows": result,
    }

def get_trader_recent_buy_count(
    trader,
    signal_ts,
    window_seconds=60
):
    cutoff = float(signal_ts) - float(window_seconds)

    conn = db()

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE trader = ?
        AND ts >= ?
        AND ts < ?
        AND side LIKE '%buy%'
        """,
        (
            trader,
            cutoff,
            float(signal_ts),
        )
    ).fetchone()

    conn.close()

    return int(row[0] or 0)

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
    current_trader,
    signal_ts=None,
):
    signal_ts = float(signal_ts if signal_ts is not None else time.time())
    cutoff = signal_ts - WINDOW


    conn = db()

    rows = conn.execute(
        """
        SELECT DISTINCT trader

        FROM trades

        WHERE mint = ?
        AND ts > ?
        AND ts <= ?
        AND (
        side LIKE '%buy%'
        OR side = 'create'
        )
        """,
        (
            mint,
            cutoff,
            signal_ts,
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


def get_consensus_trader_count(
    mint,
    signal_ts
):
    cutoff = (
        float(signal_ts)
        - WINDOW
    )

    conn = db()

    row = conn.execute(
        """
        SELECT COUNT(
            DISTINCT trader
        )

        FROM trades

        WHERE mint = ?
        AND ts >= ?
        AND ts <= ?
        AND (
            side LIKE '%buy%'
            OR side = 'create'
        )
        """,
        (
            mint,
            cutoff,
            float(signal_ts),
        )
    ).fetchone()

    conn.close()

    return int(
        row[0] or 0
    )

def get_consensus_trader_count_window(
    mint,
    signal_ts,
    window_seconds=30
):
    cutoff = (
        float(signal_ts)
        - float(window_seconds)
    )

    conn = db()

    row = conn.execute(
        """
        SELECT COUNT(
            DISTINCT trader
        )

        FROM trades

        WHERE mint = ?
        AND ts >= ?
        AND ts <= ?
        AND (
            side LIKE '%buy%'
            OR side = 'create'
        )
        """,
        (
            mint,
            cutoff,
            float(signal_ts),
        )
    ).fetchone()

    conn.close()

    return int(
        row[0] or 0
    )

def get_token_age_seconds(
    mint,
    signal_ts
):
    conn = db()

    row = conn.execute(
        """
        SELECT MIN(ts)
        FROM trades
        WHERE mint = ?
        AND side = 'create'
        AND ts <= ?
        """,
        (
            mint,
            float(signal_ts),
        )
    ).fetchone()

    conn.close()

    if not row:
        return None

    create_ts = row[0]

    if create_ts is None:
        return None

    age = float(signal_ts) - float(create_ts)

    if age < 0:
        return None

    return age


def get_trader_previous_buy_gap_seconds(
    trader,
    signal_ts
):
    conn = db()

    row = conn.execute(
        """
        SELECT MAX(ts)
        FROM trades
        WHERE trader = ?
        AND ts < ?
        AND side LIKE '%buy%'
        """,
        (
            trader,
            float(signal_ts),
        )
    ).fetchone()

    conn.close()

    if not row:
        return None

    previous_ts = row[0]

    if previous_ts is None:
        return None

    gap = float(signal_ts) - float(previous_ts)

    if gap < 0:
        return None

    return gap


# =========================================================
# SCORE: CONTEXTO GENERAL
# =========================================================
def score_market_context(signal_ts=None):

    conn = db()

    signal_ts = float(signal_ts if signal_ts is not None else time.time())
    since = signal_ts - 120

    rows = conn.execute(
        """
        SELECT
            trader,
            side,
            sol

        FROM trades

        WHERE ts >= ?
        AND ts <= ?
        AND source = 'live'
        """,
        (
            since,
            signal_ts,
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


def assess_live_model_approval(model_prediction):
    model = SHADOW_MODEL
    if model is None:
        return {
            "approved": False,
            "reason": "LIVE_MODEL_NOT_LOADED",
        }

    model_role = str(
        getattr(model, "artifact_role", "legacy") or "legacy"
    )
    if (
        not bool(getattr(model, "deployment_ready", False))
        or model_role not in {"incumbent", "legacy"}
    ):
        return {
            "approved": False,
            "reason": "LIVE_MODEL_NOT_APPROVED",
        }

    if not isinstance(model_prediction, dict):
        return {
            "approved": False,
            "reason": "LIVE_MODEL_PREDICTION_MISSING",
        }

    expected_version = str(
        getattr(model, "model_version", "") or ""
    )
    prediction_version = str(
        model_prediction.get("model_version") or ""
    )
    if not expected_version or prediction_version != expected_version:
        return {
            "approved": False,
            "reason": "LIVE_MODEL_VERSION_MISMATCH",
        }

    try:
        model_data_version = int(getattr(model, "data_version", 0) or 0)
        prediction_data_version = int(
            model_prediction.get("data_version") or 0
        )
        probability = float(model_prediction["probability"])
        threshold = float(model_prediction["threshold"])
        predicted_target = model_prediction["predicted_target"]
    except (KeyError, TypeError, ValueError):
        return {
            "approved": False,
            "reason": "LIVE_MODEL_PREDICTION_INVALID",
        }

    if (
        model_data_version != DATA_VERSION
        or prediction_data_version != DATA_VERSION
        or not math.isfinite(probability)
        or not math.isfinite(threshold)
        or not 0 <= probability <= 1
        or not 0 <= threshold <= 1
        or type(predicted_target) is not int
        or predicted_target not in (0, 1)
    ):
        return {
            "approved": False,
            "reason": "LIVE_MODEL_PREDICTION_INVALID",
        }

    if predicted_target != 1 or probability < threshold:
        return {
            "approved": False,
            "reason": "LIVE_MODEL_REJECTED",
            "model_version": prediction_version,
            "probability": probability,
            "threshold": threshold,
        }

    return {
        "approved": True,
        "reason": "LIVE_MODEL_APPROVED",
        "model_version": prediction_version,
        "probability": probability,
        "threshold": threshold,
    }


def maybe_execute_live_copy(
    signal_id,
    decision,
    trader,
    event,
    source,
    price_at_signal,
    market_cap,
    model_prediction,
):
    if str(source or "").strip().lower() != "live":
        return {"attempted": False, "reason": "NON_LIVE_SOURCE"}

    if decision != "COPY":
        return {"attempted": False, "reason": "DECISION_NOT_COPY"}

    if trader in OBSERVE_TRADERS:
        return {"attempted": False, "reason": "TRADER_OBSERVE_ONLY"}

    model_approval = assess_live_model_approval(model_prediction)
    if not model_approval["approved"]:
        return {
            "attempted": False,
            **model_approval,
        }

    # El dinero real solo sigue a traders efectivamente medidos. Un trader
    # sin evidencia suficiente puntúa cerca del neutral, pero eso es un prior,
    # no una medición: en papel se lo sigue observando para acumular
    # evidencia, en real no se lo copia hasta que esté calificado.
    trader_quality = get_trader_quality_assessment(trader)
    if not trader_quality["rated"]:
        return {
            "attempted": False,
            "reason": "LIVE_TRADER_NOT_RATED",
            "trader_samples": trader_quality["samples"],
            "minimum_samples": TRADER_QUALITY_MIN_SAMPLES,
        }

    readiness = get_live_execution_readiness(
        "buy",
        trader=trader,
        amount_usd=LIVE_BUY_USD,
    )
    if not readiness["ready"]:
        return {
            "attempted": False,
            "reason": "LIVE_BUY_NOT_READY",
            "blockers": readiness.get("blockers", []),
        }

    try:
        signal_id = int(signal_id)
    except (TypeError, ValueError):
        signal_id = 0
    if signal_id <= 0:
        return {"attempted": False, "reason": "LIVE_SIGNAL_ID_INVALID"}

    try:
        signal_price = float(price_at_signal)
    except (TypeError, ValueError):
        signal_price = 0.0
    if not math.isfinite(signal_price) or signal_price <= 0:
        return {"attempted": False, "reason": "LIVE_SIGNAL_PRICE_INVALID"}

    try:
        liquidity_sol = float(
            (event or {}).get("vSolInBondingCurve")
            or 0
        )
    except (TypeError, ValueError):
        liquidity_sol = 0.0
    if not validate_liquidity(liquidity_sol):
        return {
            "attempted": False,
            "reason": "LIVE_SIGNAL_LIQUIDITY_INVALID",
        }

    try:
        result = execute_pumpportal_lightning_buy(
            mint=str((event or {}).get("mint") or "").strip(),
            expected_price=signal_price,
            execution_price=None,
            liquidity_sol=liquidity_sol,
            amount_usd=LIVE_BUY_USD,
            idempotency_key=f"copy-evaluation-{signal_id}",
            market_cap_sol=market_cap,
            origin_trader=trader,
        )
    except Exception as exc:
        print("[LIVE BUY] Dispatch failed:", repr(exc))
        return {
            "attempted": True,
            "ok": False,
            "reason": "LIVE_BUY_DISPATCH_FAILED",
        }

    return {
        **result,
        "attempted": True,
        "model_approval": model_approval,
    }


# =========================================================
# ANALIZAR COMPRA
# =========================================================

def evaluate_buy(
    trader,
    event,
    source="live",
    price_at_signal=0.0,
    allow_live_buys=True,
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
    event_index = market_event_index(event)

    # Momento on-chain del evento que puede abrir la posición. Se guarda como
    # referencia de entrada para poder descartar después operaciones anteriores
    # a ella. `opened_ts` no sirve: mide cuándo reaccionamos nosotros.
    entry_block_event_ts = market_event_block_ts(event)
    signal_ts = entry_block_event_ts or time.time()


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
        trader,
        signal_ts=signal_ts,
    )


    market_score = score_market_context(signal_ts=signal_ts)


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
        cursor = conn.execute(
            """
            INSERT INTO evaluations(

                trade_signature,

                event_index,

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

                market_cap,
                
                sol_amount,

                data_version,

                reasons

            )

            VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                signature,

                event_index,

                signal_ts,

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

                market_cap,

                sol_amount,

                DATA_VERSION,
                json.dumps(reasons)
            )
        )

        signal_id = cursor.lastrowid

        conn.commit()

        create_signal_outcome(
            signal_id=signal_id,
            mint=mint,
            trader=trader,
            signal_ts=signal_ts,
            price_at_signal=price_at_signal
        )

        model_prediction = None
        if market_cap > 0 and sol_amount > 0 and price_at_signal > 0:
            model_prediction = observe_shadow_signal(
                evaluation_id=signal_id,
                connection=conn,
                trader=trader,
                mint=mint,
                signal_ts=signal_ts,
                trader_score=trader_score,
                timing_score=timing_score,
                size_score=size_score,
                token_score=token_score,
                consensus_score=consensus_score,
                market_score=market_score,
                score_total=score,
                market_cap=market_cap,
                sol_amount=sol_amount,
                price_at_signal=price_at_signal,
            )

        if mint and not mint.startswith("DEMO"):
            TRACKED_TOKENS.add(mint)

    except sqlite3.IntegrityError:
        conn.close()

        return {
            "score": score,
            "decision": decision,
            "reasons": reasons
        }

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
    # La ejecución real se evalúa por separado y permanece
    # protegida por todos los controles de live trading.

    if (
    decision == "COPY"
    and trader not in OBSERVE_TRADERS
):

        open_paper_position(
        mint=mint,
        trader=trader,
        market_cap=market_cap,
        score=score,
        decision=decision,
        entry_block_event_ts=entry_block_event_ts
    )

    if allow_live_buys:
        live_execution = maybe_execute_live_copy(
            signal_id=signal_id,
            decision=decision,
            trader=trader,
            event=event,
            source=source,
            price_at_signal=price_at_signal,
            market_cap=market_cap,
            model_prediction=model_prediction,
        )
    else:
        live_execution = {
            "attempted": False,
            "reason": "TRANSPORT_LIVE_BUYS_DISABLED",
        }

    return {
        "score": score,
        "decision": decision,
        "reasons": reasons,
        "live_execution": live_execution,
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
    mode="paper",
    entry_block_event_ts=None
):

    if market_cap <= 0:
        return

    # Se valida acá, al entrar, y no al usarse: si llega roto, el problema está
    # en quien abre la posición y conviene verlo ahí y no eventos después.
    entry_block_event_ts = validated_block_event_ts(
        entry_block_event_ts,
        origen="entry_block_event_ts",
    )

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
    try:
        conn.execute("BEGIN IMMEDIATE")

        daily_pnl = get_daily_realized_pnl(
            mode=mode,
            connection=conn,
        )

        if daily_pnl <= -MAX_DAILY_LOSS_USD:
            print(
                f"[RISK BLOCK] No se abre {mint}: "
                f"pérdida diaria ${daily_pnl:.2f} "
                f"alcanzó el límite "
                f"-${MAX_DAILY_LOSS_USD:.2f}"
            )
            conn.rollback()
            return

        if count_open_positions(
            mode=mode,
            connection=conn,
        ) >= 1:
            print(
                f"[RISK BLOCK] No se abre {mint}: "
                f"ya existe una posición abierta"
            )
            conn.rollback()
            return

        exists = conn.execute(
            """
            SELECT id
            FROM paper_positions
            WHERE mint = ?
            AND status = 'open'
            AND mode = ?
            """,
            (mint, mode)
        ).fetchone()

        if exists:
            conn.rollback()
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
                mode,
                entry_block_event_ts,
                last_applied_block_event_ts
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
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
                mode,
                entry_block_event_ts,
                entry_block_event_ts
            )
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
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



def untrack_token_if_unused(mint):
    if not mint:
        return False
    conn = db()
    needed = conn.execute(
        """SELECT 1 FROM paper_positions
           WHERE mint = ? AND status = 'open'
           UNION ALL
           SELECT 1 FROM live_positions
           WHERE mint = ? AND status = 'open'
           UNION ALL
           SELECT 1 FROM signal_outcomes
           WHERE mint = ? AND status = 'active'
           LIMIT 1""",
        (mint, mint, mint),
    ).fetchone()
    conn.close()
    if needed:
        return False
    TOKENS_TO_UNSUBSCRIBE.add(mint)
    TRACKED_TOKENS.discard(mint)
    SUBSCRIBED_TOKENS.discard(mint)
    return True


def decide_live_position_exit(
    original_amount_raw,
    remaining_amount_raw,
    entry_market_cap_sol,
    current_market_cap_sol,
    origin_trader,
    event_trader,
    side,
    new_token_balance,
    tp_stage,
):
    original = int(original_amount_raw)
    remaining = int(remaining_amount_raw)
    entry_market_cap = float(entry_market_cap_sol or 0)
    current_market_cap = float(current_market_cap_sol or 0)
    stage = int(tp_stage or 0)
    if (original <= 0 or remaining <= 0
            or not math.isfinite(entry_market_cap)
            or not math.isfinite(current_market_cap)
            or entry_market_cap <= 0 or current_market_cap <= 0):
        return None

    change_pct = current_market_cap / entry_market_cap - 1
    try:
        observed_balance = (
            float(new_token_balance)
            if new_token_balance is not None
            else None
        )
    except (TypeError, ValueError):
        observed_balance = None
    balance_is_known = (
        observed_balance is not None
        and math.isfinite(observed_balance)
    )
    is_origin_sell = (
        "sell" in str(side or "").lower()
        and str(event_trader or "") == str(origin_trader or "")
        and bool(origin_trader)
    )
    if change_pct <= -0.20:
        return {"token_amount_raw": str(remaining), "reason": "STOP_LOSS",
                "target_tp_stage": None, "change_pct": change_pct}
    if (
        is_origin_sell
        and balance_is_known
        and observed_balance is not None
        and observed_balance <= 0
    ):
        return {"token_amount_raw": str(remaining), "reason": "TRADER_EXIT",
                "target_tp_stage": None, "change_pct": change_pct}

    target_stage = 0
    if change_pct >= 1.00:
        target_stage = 3
    elif change_pct >= 0.50:
        target_stage = 2
    elif change_pct >= 0.25:
        target_stage = 1
    if target_stage > stage:
        pending_stages = target_stage - stage
        amount = min(remaining, original * pending_stages // 4)
        if amount > 0:
            return {"token_amount_raw": str(amount), "reason": "TAKE_PROFIT",
                    "target_tp_stage": target_stage, "change_pct": change_pct}

    if is_origin_sell:
        amount = min(remaining, max(1, original // 4))
        return {"token_amount_raw": str(amount), "reason": "TRADER_PARTIAL",
                "target_tp_stage": None, "change_pct": change_pct}
    return None


def evaluate_live_position_exit(
    mint,
    trader,
    side,
    market_cap,
    new_token_balance,
    event_signature="",
    event_index=0,
    event_block_event_ts=None,
):
    current_market_cap = float(market_cap or 0)
    if not mint or not math.isfinite(current_market_cap) or current_market_cap <= 0:
        return []
    event_ts = validated_block_event_ts(
        event_block_event_ts,
        origen="live_exit_event.blockEventTs",
    )
    # Se calcula una sola vez, antes de decidir nada: un índice inválido es un
    # bug del parser y conviene que falle igual de fuerte sin importar qué
    # decisión salga. Sin firma devuelve None, que no es un error: es la ruta
    # que todavía no tiene identidad para ofrecer.
    event_identity = market_event_identity(event_signature, event_index)
    results = []
    accepted_rows = []
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT order_id, token_amount_raw, remaining_amount_raw,
                      entry_market_cap_sol, origin_trader, tp_stage,
                      entry_block_time, last_applied_block_event_ts
               FROM live_positions
               WHERE mint = ? AND status = 'open'
               ORDER BY order_id""",
            (mint,),
        ).fetchall()
        for row in rows:
            try:
                entry_ts = validated_block_event_ts(
                    row[6],
                    origen="live_positions.entry_block_time",
                )
                last_ts = validated_block_event_ts(
                    row[7],
                    origen="live_positions.last_applied_block_event_ts",
                )
            except ValueError:
                results.append({
                    "ok": False,
                    "position_order_id": row[0],
                    "reason": "INVALID_LIVE_POSITION_TIMESTAMP",
                })
                continue
            if event_ts is not None and entry_ts is not None and event_ts < entry_ts:
                results.append({
                    "ok": False,
                    "position_order_id": row[0],
                    "reason": "EVENT_BEFORE_LIVE_ENTRY",
                })
                continue
            if event_ts is not None and last_ts is not None and event_ts < last_ts:
                results.append({
                    "ok": False,
                    "position_order_id": row[0],
                    "reason": "EVENT_BEFORE_LAST_LIVE_EVENT",
                })
                continue
            conn.execute(
                "UPDATE live_positions SET current_market_cap_sol = ?, "
                "last_applied_block_event_ts = COALESCE(?, last_applied_block_event_ts) "
                "WHERE order_id = ? AND status = 'open'",
                (current_market_cap, event_ts, row[0]),
            )
            accepted_rows.append(row)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for row in accepted_rows:
        decision = decide_live_position_exit(
            row[1], row[2], row[3], current_market_cap, row[4], trader, side,
            new_token_balance, row[5],
        )
        if not decision:
            continue
        reason = decision["reason"]
        if reason == "TRADER_PARTIAL":
            # Una venta parcial por operación del trader de origen. Con la
            # firma sola, dos operaciones de una misma transacción compartían
            # clave y la segunda se descartaba como reuso idempotente: una
            # venta que debía ocurrir y no ocurría. El índice las separa, y el
            # mismo evento reintentado sigue siendo una sola.
            if event_identity is None:
                results.append({"ok": False, "position_order_id": row[0],
                                "reason": "EVENT_SIGNATURE_REQUIRED"})
                continue
            suffix = f"{reason}-{event_identity}"
        elif reason == "TAKE_PROFIT":
            # Idempotente por etapa, no por evento: el escalón se vende una
            # vez aunque lleguen muchos eventos con el precio arriba.
            suffix = f"TP-{decision['target_tp_stage']}"
        else:
            # STOP_LOSS y TRADER_EXIT cierran la posición entera: idempotentes
            # por cierre, una sola vez por posición.
            suffix = reason
        result = execute_pumpportal_lightning_sell(
            position_order_id=row[0],
            token_amount_raw=decision["token_amount_raw"],
            idempotency_key=f"LIVE-EXIT-{row[0]}-{suffix}",
            exit_reason=reason,
            target_tp_stage=decision["target_tp_stage"],
            event_block_event_ts=event_ts,
        )
        results.append({"position_order_id": row[0], **result})
    return results


def market_event_identity(signature, event_index=0):
    """Identidad de un evento de mercado dentro de una transacción.

    La usan las dos rutas que tienen que distinguir operaciones: las posiciones
    paper y las salidas live. Compartir la función es lo que garantiza que el
    formato sea el mismo en las dos.

    La firma sola no alcanza: una transacción puede contener varias
    operaciones Pump válidas, cada una con su `event_index`. Usar solo la
    firma haría que la segunda se descartara como duplicado, perdiéndola.

    Un índice inválido se rechaza en vez de convertirse en 0: convertirlo
    silenciosamente haría que dos operaciones distintas de la misma
    transacción compartieran identidad, y la segunda se perdería como
    duplicado. Justo el error que esta función existe para evitar. El índice
    lo produce nuestro propio parser, así que un valor inválido es un bug, y
    conviene que se vea.
    """
    signature = str(signature or "").strip()

    if not signature:
        return None

    # `isinstance(True, int)` es verdadero en Python, y `f"{True}"` da "True".
    if isinstance(event_index, bool) or not isinstance(event_index, int):
        raise ValueError(
            "event_index debe ser un entero; "
            f"llegó {event_index!r} ({type(event_index).__name__})"
        )

    if event_index < 0:
        raise ValueError(
            f"event_index no puede ser negativo; llegó {event_index!r}"
        )

    return f"{signature}:{event_index}"


def market_event_index(event, required=False):
    """Índice de una operación dentro de su transacción, leído del evento.

    La identidad viaja adentro del evento y no como parámetro aparte a
    propósito: cuando la ingesta normalizada tenga cola y reintentos, el
    evento se va a guardar y releer, y un índice que viajara al lado se
    perdería en ese salto mientras la firma sobrevive. Firma e índice son dos
    mitades de una misma identidad y tienen que moverse juntas.

    Ausente significa cosas distintas según de dónde venga el evento, y por eso
    existe ``required``:

    - llegando en vivo por PumpPortal, ausente es legítimo y vale 0: ese
      transporte entrega una operación por mensaje y no manda índice;
    - saliendo de un evento normalizado nuestro —al serializarlo o al
      reconstruirlo desde disco—, ausente significa que el índice se perdió en
      el camino. Ahí `required=True`, porque devolver 0 le daría la misma
      identidad a dos operaciones distintas y la segunda se descartaría como
      duplicado, en silencio.

    Presente pero inválido se rechaza siempre: ahí hay un parser equivocado.
    """
    if not isinstance(event, dict):
        if required:
            raise ValueError(
                "el evento debe ser un objeto; "
                f"llegó {type(event).__name__}"
            )
        return 0

    raw = event.get("eventIndex")

    if raw is None:
        raw = event.get("event_index")

    if raw is None:
        if required:
            raise ValueError(
                "el evento normalizado no trae eventIndex: se perdió en el "
                "camino. Convertirlo en 0 haría colisionar dos operaciones "
                "de la misma transacción."
            )
        return 0

    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(
            f"eventIndex debe ser un entero; llegó {raw!r} "
            f"({type(raw).__name__})"
        )

    if raw < 0:
        raise ValueError(f"eventIndex no puede ser negativo; llegó {raw!r}")

    return raw


def validated_block_event_ts(value, origen="blockEventTs"):
    """Timestamp on-chain validado: numérico, finito y positivo.

    ``None`` pasa como ``None``: desconocido no es un error. PumpPortal no
    manda timestamp, y las posiciones abiertas antes de que esto existiera
    tampoco lo tienen.
    """
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{origen} debe ser numérico; llegó {value!r} "
            f"({type(value).__name__})"
        )

    numero = float(value)

    if not math.isfinite(numero) or numero <= 0:
        raise ValueError(
            f"{origen} debe ser finito y positivo; llegó {value!r}"
        )

    return numero


def market_event_block_ts(event):
    """Momento on-chain del evento, leído de adentro del evento.

    Ausente significa desconocido y devuelve ``None``: quien lo use decide qué
    hacer con esa falta. Presente pero inservible se rechaza, porque ahí hay un
    parser equivocado.
    """
    if not isinstance(event, dict):
        return None

    raw = event.get("blockEventTs")

    if raw is None:
        raw = event.get("block_event_ts")

    return validated_block_event_ts(raw)


def market_event_new_token_balance(event):
    """Preserva la diferencia entre un saldo desconocido y un cero real.

    PumpPortal históricamente omitió el campo y el pipeline lo interpretó como
    cero. El parser normalizado de Helius lo incluye con ``None`` cuando no se
    puede reconstruir; convertir ese valor a cero fabricaría una salida total.
    """
    raw = event.get("newTokenBalance")
    if raw is None:
        return None

    try:
        if isinstance(raw, bool):
            raise TypeError("bool")
        balance = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_NEW_TOKEN_BALANCE") from exc
    if not math.isfinite(balance) or balance < 0:
        raise ValueError("INVALID_NEW_TOKEN_BALANCE")
    return balance


def stored_block_event_ts(value):
    """Lee un timestamp que guardamos nosotros; inservible cuenta como ausente.

    Asimetría deliberada con `validated_block_event_ts()`. Un valor inválido
    que entra desde el parser es un bug y conviene que estalle apenas aparece.
    Uno ya guardado en la base es distinto: hacerlo estallar rompería esa
    posición en cada evento que llegue, para siempre. Sin referencia confiable
    la guarda se apaga y queda el comportamiento previo, que es la misma regla
    que ya rige para un dato ausente.
    """
    try:
        if value is None or isinstance(value, bool):
            return None
        numero = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(numero) or numero <= 0:
        return None

    return numero


def market_event_from_inbox_row(
    event_json,
    wallet,
    signature,
    event_index,
    block_event_ts,
):
    """Reconstruye el evento normalizado guardado en `market_event_inbox`.

    La fila guarda el evento serializado y, al lado, las columnas por las que
    se consulta. Reconstruir es volver a juntar las dos partes, y verificar que
    no se hayan separado: el JSON es la fuente de la identidad y las columnas
    son una copia derivada, así que si discrepan hay una fila escrita por
    código viejo o corrompida, y aplicarla sería peor que rechazarla.

    Todo es obligatorio a propósito. La fila siempre tiene las cinco cosas, y
    cada una que falte produce un error silencioso distinto. Las filas viejas
    pueden no llevar la billetera dentro del JSON; las nuevas la llevan y debe
    coincidir con la columna derivada.
    """
    event = json.loads(event_json)

    if not isinstance(event, dict):
        raise ValueError(
            "event_json debe ser un objeto; "
            f"llegó {type(event).__name__}"
        )

    wallet = str(wallet or "").strip()

    if not wallet:
        raise ValueError(
            "la fila debe traer la billetera que firmó: el evento normalizado "
            "la necesita para elegir la ruta correcta"
        )

    signature = str(signature or "").strip()

    if not signature:
        raise ValueError("la fila debe traer la firma de la transacción")

    # Identidad del JSON, que es la que se va a usar al aplicarlo.
    indice_del_json = market_event_index(event, required=True)
    firma_del_json = str(event.get("signature") or "").strip()

    if firma_del_json != signature:
        raise ValueError(
            "la firma de la fila y la del evento no coinciden: "
            f"{signature!r} contra {firma_del_json!r}"
        )

    if indice_del_json != event_index:
        raise ValueError(
            "el índice de la fila y el del evento no coinciden: "
            f"{event_index!r} contra {indice_del_json!r}"
        )

    timestamp_del_json = market_event_block_ts(event)
    timestamp_de_la_fila = validated_block_event_ts(
        block_event_ts,
        origen="market_event_inbox.block_event_ts",
    )

    if timestamp_del_json is None or timestamp_de_la_fila is None:
        raise ValueError(
            "el timestamp on-chain es obligatorio al reconstruir el inbox"
        )

    if timestamp_del_json != timestamp_de_la_fila:
        raise ValueError(
            "el timestamp de la fila y el del evento no coinciden: "
            f"{timestamp_de_la_fila!r} contra {timestamp_del_json!r}"
        )

    wallet_del_json = str(event.get("traderPublicKey") or "").strip()
    if wallet_del_json and wallet_del_json != wallet:
        raise ValueError(
            "la billetera de la fila y la del evento no coinciden: "
            f"{wallet!r} contra {wallet_del_json!r}"
        )

    # Un saldo inservible es un parser equivocado: mejor que falle acá, en
    # validación, que en el consumidor después de reservar la identidad.
    market_event_new_token_balance(event)

    event["traderPublicKey"] = wallet

    return event


def get_market_event_inbox_activation_ts(connection=None):
    """Lee la frontera persistida del consumidor, o ``None`` si no se activó."""
    conn = connection or db()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (MARKET_EVENT_INBOX_ACTIVATION_STATE_KEY,),
        ).fetchone()
    finally:
        if connection is None:
            conn.close()

    if not row:
        return None

    try:
        activation_ts = float(row[0])
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_MARKET_EVENT_INBOX_ACTIVATION_TS") from exc

    if not math.isfinite(activation_ts) or activation_ts <= 0:
        raise ValueError("INVALID_MARKET_EVENT_INBOX_ACTIVATION_TS")

    return activation_ts


def establish_market_event_inbox_activation(now=None):
    """Fija una sola vez desde cuándo el inbox puede producir efectos."""
    activation_ts = float(now if now is not None else time.time())
    if not math.isfinite(activation_ts) or activation_ts <= 0:
        raise ValueError("INVALID_MARKET_EVENT_INBOX_ACTIVATION_TS")

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR IGNORE INTO app_state(key, value) VALUES(?, ?)",
            (
                MARKET_EVENT_INBOX_ACTIVATION_STATE_KEY,
                repr(activation_ts),
            ),
        )
        stored = get_market_event_inbox_activation_ts(connection=conn)
        conn.commit()
        return stored
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def market_event_is_after_activation(
    received_ts,
    block_event_ts,
    activation_ts,
):
    """Permite solo eventos recibidos y ocurridos desde la activación.

    `blockEventTs` tiene resolución de segundos, por eso se acepta todo el
    segundo en que se fijó la frontera. Un dato ausente o inválido falla
    cerrado: el consumidor no debe convertir historia incierta en efectos.
    """
    try:
        received_ts = float(received_ts)
        block_event_ts = float(block_event_ts)
        activation_ts = float(activation_ts)
    except (TypeError, ValueError):
        return False

    if not all(math.isfinite(value) and value > 0 for value in (
        received_ts,
        block_event_ts,
        activation_ts,
    )):
        return False

    return (
        received_ts >= activation_ts
        and block_event_ts >= math.floor(activation_ts)
    )


def claim_market_event_inbox_validation_batch(limit=None, now=None):
    """Reserva eventos observados para validarlos sin ejecutar sus efectos.

    La reserva tiene vencimiento para que un reinicio no deje filas atrapadas.
    El token impide que un worker lento confirme una fila que otro worker ya
    recuperó después del vencimiento.
    """
    limit = int(limit or MARKET_EVENT_INBOX_VALIDATION_BATCH_SIZE)
    limit = max(1, min(200, limit))
    now = float(now if now is not None else time.time())
    stale_before = now - MARKET_EVENT_INBOX_VALIDATION_LEASE_SECONDS
    claim_token = secrets.token_hex(16)
    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT signature, event_index, wallet, event_json, block_event_ts
            FROM market_event_inbox
            WHERE status = 'observed'
            OR (
                status = 'validating'
                AND COALESCE(claimed_ts, 0) <= ?
            )
            ORDER BY received_ts, signature, event_index
            LIMIT ?
            """,
            (stale_before, limit),
        ).fetchall()

        for signature, event_index, _, _, _ in rows:
            conn.execute(
                """
                UPDATE market_event_inbox
                SET status = 'validating',
                    attempts = attempts + 1,
                    claim_token = ?,
                    claimed_ts = ?,
                    processed_ts = NULL,
                    last_error = NULL
                WHERE signature = ? AND event_index = ?
                """,
                (claim_token, now, signature, event_index),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return [
        {
            "signature": row[0],
            "event_index": row[1],
            "wallet": row[2],
            "event_json": row[3],
            "block_event_ts": row[4],
            "claim_token": claim_token,
        }
        for row in rows
    ]


def finish_market_event_inbox_validation(
    signature,
    event_index,
    claim_token,
    status,
    error=None,
    now=None,
):
    """Finaliza una reserva solo si todavía pertenece al mismo worker."""
    if status not in {"validated", "rejected"}:
        raise ValueError(f"estado final de validación inválido: {status!r}")

    now = float(now if now is not None else time.time())
    error_text = str(error or "")[:500] or None
    conn = db()

    try:
        cursor = conn.execute(
            """
            UPDATE market_event_inbox
            SET status = ?,
                processed_ts = ?,
                last_error = ?,
                claim_token = NULL,
                claimed_ts = NULL
            WHERE signature = ?
            AND event_index = ?
            AND status = 'validating'
            AND claim_token = ?
            """,
            (
                status,
                now,
                error_text,
                signature,
                event_index,
                claim_token,
            ),
        )
        conn.commit()
        return bool(cursor.rowcount)
    finally:
        conn.close()


def validate_market_event_inbox_once(limit=None, now=None):
    """Valida un lote del inbox sin llamar al router ni aplicar efectos."""
    rows = claim_market_event_inbox_validation_batch(limit=limit, now=now)
    result = {
        "claimed": len(rows),
        "validated": 0,
        "rejected": 0,
        "lost_claims": 0,
    }

    for row in rows:
        try:
            market_event_from_inbox_row(
                row["event_json"],
                wallet=row["wallet"],
                signature=row["signature"],
                event_index=row["event_index"],
                block_event_ts=row["block_event_ts"],
            )
            status = "validated"
            error = None
        except Exception as exc:
            status = "rejected"
            error = f"{exc.__class__.__name__}: {exc}"

        finished = finish_market_event_inbox_validation(
            row["signature"],
            row["event_index"],
            row["claim_token"],
            status,
            error=error,
            now=now,
        )

        if finished:
            result[status] += 1
        else:
            result["lost_claims"] += 1

    return result


async def market_event_inbox_validation_worker():
    """Valida continuamente el inbox; nunca enruta eventos."""
    while True:
        try:
            await asyncio.to_thread(validate_market_event_inbox_once)
        except Exception as exc:
            print("[HELIUS INBOX VALIDATION]", repr(exc))

        await asyncio.sleep(MARKET_EVENT_INBOX_VALIDATION_POLL_SECONDS)


def claim_market_event_inbox_processing_batch(limit=None, now=None):
    """Reserva filas validadas para un único consumidor con lease."""
    limit = int(limit or MARKET_EVENT_INBOX_CONSUMER_BATCH_SIZE)
    limit = max(1, min(200, limit))
    now = float(now if now is not None else time.time())
    stale_before = now - MARKET_EVENT_INBOX_CONSUMER_LEASE_SECONDS
    claim_token = secrets.token_hex(16)
    conn = db()

    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT
                signature,
                event_index,
                wallet,
                event_json,
                received_ts,
                block_event_ts
            FROM market_event_inbox
            WHERE status = 'validated'
            OR (
                status = 'processing'
                AND COALESCE(claimed_ts, 0) <= ?
            )
            ORDER BY received_ts, signature, event_index
            LIMIT ?
            """,
            (stale_before, limit),
        ).fetchall()

        for signature, event_index, _, _, _, _ in rows:
            conn.execute(
                """
                UPDATE market_event_inbox
                SET status = 'processing',
                    attempts = attempts + 1,
                    claim_token = ?,
                    claimed_ts = ?,
                    processed_ts = NULL,
                    last_error = NULL
                WHERE signature = ? AND event_index = ?
                """,
                (claim_token, now, signature, event_index),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return [
        {
            "signature": row[0],
            "event_index": row[1],
            "wallet": row[2],
            "event_json": row[3],
            "received_ts": row[4],
            "block_event_ts": row[5],
            "claim_token": claim_token,
        }
        for row in rows
    ]


def finish_market_event_inbox_processing(
    signature,
    event_index,
    claim_token,
    status,
    error=None,
    now=None,
):
    """Cierra la reserva solo si el worker todavía la posee.

    ``validated`` devuelve la fila a la cola con el error anotado: es para
    fallas transitorias anteriores a reservar la identidad global, donde no
    hubo ningún efecto y reintentar es seguro. Los demás estados son finales.
    """
    final_statuses = {
        "processed",
        "duplicate",
        "ignored_pre_activation",
        "failed",
        "validated",
    }
    if status not in final_statuses:
        raise ValueError(f"estado final de consumo inválido: {status!r}")

    now = float(now if now is not None else time.time())
    processed_ts = None if status == "validated" else now
    error_text = str(error or "")[:500] or None
    conn = db()

    try:
        cursor = conn.execute(
            """
            UPDATE market_event_inbox
            SET status = ?,
                processed_ts = ?,
                last_error = ?,
                claim_token = NULL,
                claimed_ts = NULL
            WHERE signature = ?
            AND event_index = ?
            AND status = 'processing'
            AND claim_token = ?
            """,
            (
                status,
                processed_ts,
                error_text,
                signature,
                event_index,
                claim_token,
            ),
        )
        conn.commit()
        return bool(cursor.rowcount)
    finally:
        conn.close()


def consume_market_event_inbox_once(limit=None, now=None):
    """Enruta una vez los eventos validados posteriores a la activación.

    La identidad global se reserva antes de ejecutar efectos, igual que en el
    stream. Si el router falla, la fila queda terminalmente fallida: reintentar
    una ruta que pudo alcanzar el camino del dinero sería menos seguro que
    hacer visible la pérdida para revisión manual.

    La única falla que se reintenta es la de la reserva misma —un error de base
    al escribir `processed_market_events`—: ahí no hubo efecto alguno y la fila
    vuelve a `validated` con el error anotado. Una fila que no se puede
    reconstruir es determinista y queda fallida; reintentarla no la arregla.
    """
    activation_ts = get_market_event_inbox_activation_ts()
    if activation_ts is None:
        raise RuntimeError("MARKET_EVENT_INBOX_ACTIVATION_NOT_ESTABLISHED")

    rows = claim_market_event_inbox_processing_batch(limit=limit, now=now)
    result = {
        "claimed": len(rows),
        "processed": 0,
        "duplicates": 0,
        "ignored_pre_activation": 0,
        "failed": 0,
        "released": 0,
        "lost_claims": 0,
    }

    for row in rows:
        reserving = False
        try:
            if not market_event_is_after_activation(
                received_ts=row["received_ts"],
                block_event_ts=row["block_event_ts"],
                activation_ts=activation_ts,
            ):
                status = "ignored_pre_activation"
                error = "PRE_ACTIVATION_OR_UNTRUSTED_TIMESTAMP"
            else:
                event = market_event_from_inbox_row(
                    row["event_json"],
                    wallet=row["wallet"],
                    signature=row["signature"],
                    event_index=row["event_index"],
                    block_event_ts=row["block_event_ts"],
                )

                reserving = True
                reserved = mark_market_event_processed(
                    row["signature"],
                    row["event_index"],
                    source="helius",
                )
                reserving = False

                if not reserved:
                    status = "duplicate"
                    error = None
                else:
                    event_id = market_event_identity(
                        row["signature"], row["event_index"]
                    )
                    SEEN_EVENT_IDS.add(event_id)
                    if len(SEEN_EVENT_IDS) > 5000:
                        SEEN_EVENT_IDS.clear()
                    route_market_event(
                        event,
                        allow_live_buys=False,
                        allow_live_exits=True,
                    )
                    status = "processed"
                    error = None
        except Exception as exc:
            # Si la reserva lanzó, su transacción hizo rollback y la identidad
            # sigue libre: no hubo efectos y se puede reintentar.
            status = "validated" if reserving else "failed"
            error = f"{exc.__class__.__name__}: {exc}"

        finished = finish_market_event_inbox_processing(
            row["signature"],
            row["event_index"],
            row["claim_token"],
            status,
            error=error,
            now=now,
        )

        if not finished:
            result["lost_claims"] += 1
        elif status == "duplicate":
            result["duplicates"] += 1
        elif status == "validated":
            result["released"] += 1
        else:
            result[status] += 1

    return result


async def market_event_inbox_consumer_worker():
    """Consume continuamente el inbox después de una activación persistente."""
    while True:
        try:
            result = await asyncio.to_thread(consume_market_event_inbox_once)
            if result["released"]:
                print(
                    "[HELIUS INBOX CONSUMER] "
                    f"{result['released']} evento(s) devueltos para reintento"
                )
            if result["failed"]:
                await send_discord_alert(
                    "Pump Copilot: Helius inbox tuvo "
                    f"{result['failed']} evento(s) fallido(s)."
                )
        except Exception as exc:
            print("[HELIUS INBOX CONSUMER]", repr(exc))

        await asyncio.sleep(MARKET_EVENT_INBOX_CONSUMER_POLL_SECONDS)


def decide_paper_position_action(
    change_pct,
    remaining,
    tp_stage,
    side,
    trader,
    origin_trader,
    new_token_balance,
):
    """Decide qué hacer con una posición paper. No toca la base de datos.

    Separado de la escritura para que el razonamiento sea verificable por sí
    solo y para que la transacción quede acotada, igual que la pareja
    `decide_live_position_exit` / `evaluate_live_position_exit`.
    """
    action = "HOLD"
    exit_reason = ""
    sell_fraction = 0.0

    if change_pct <= -0.20:
        action = "STOP LOSS"
        sell_fraction = remaining
        exit_reason = "Caída del 20% desde la entrada"

    elif (
        "sell" in side
        and trader == origin_trader
        and new_token_balance is not None
        and float(new_token_balance) <= 0
    ):
        action = "EXIT"
        sell_fraction = remaining
        exit_reason = f"@{origin_trader} cerró su posición"

    elif change_pct >= 0.25 and tp_stage < 3:
        if change_pct >= 1.00:
            target_stage = 3
        elif change_pct >= 0.50:
            target_stage = 2
        else:
            target_stage = 1

        missing_stages = target_stage - tp_stage

        # Cada nivel vende 25% de la posición original. Si el precio salta
        # varios niveles, se ejecutan todos los tramos pendientes.
        sell_fraction = min(0.25 * missing_stages, remaining)
        tp_stage = target_stage

        if target_stage == 3:
            action = "TAKE PROFIT +100%"
        elif target_stage == 2:
            action = "TAKE PROFIT +50%"
        else:
            action = "TAKE PROFIT +25%"

    elif "sell" in side and trader == origin_trader:
        action = "PARTIAL SELL"
        sell_fraction = min(0.25, remaining)

    return {
        "action": action,
        "sell_fraction": sell_fraction,
        "exit_reason": exit_reason,
        "tp_stage": tp_stage,
    }


def apply_paper_event(
    mint,
    trader,
    side,
    market_cap,
    new_token_balance,
    event_id,
    event_block_event_ts=None,
):
    """Aplica el evento en una sola transacción y describe qué pasó.

    Deliberadamente no toca nada fuera de la base: la limpieza posterior al
    cierre abre su propia conexión y se bloquearía contra el lock de escritura
    que esta sostiene. Vive en `update_paper_position()`, después del commit.
    """

    conn = db()

    # Toda la vida de la conexión va dentro del try: `BEGIN IMMEDIATE` toma el
    # lock de escritura de entrada, así que una excepción en la consulta o en
    # los cálculos dejaría la conexión abierta sosteniéndolo y bloquearía a los
    # demás escritores.
    try:
        conn.execute("BEGIN IMMEDIATE")

        position = conn.execute(
            """
            SELECT
                id,
                entry_mc,
                stake_usd,
                remaining_pct,
                realized_pnl_usd,
                origin_trader,
                tp_stage,
                entry_block_event_ts,
                last_applied_block_event_ts
            FROM paper_positions
            WHERE mint = ?
            AND status = 'open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (mint,),
        ).fetchone()

        if not position:
            # Que no haya posición abierta no siempre significa que no haya
            # nada que hacer: puede que este mismo evento la haya cerrado en un
            # intento anterior y que la limpieza posterior al commit —que abre
            # otra conexión— haya fallado. El reintento no encontraría la
            # posición y daría la limpieza por hecha, dejando el token suscripto
            # para siempre sin nada que lo justifique.
            #
            # La limpieza es idempotente, así que repetirla es gratis y no
            # repetirla no se arregla después.
            cerrada_por_este_evento = False

            if event_id is not None:
                cerrada_por_este_evento = bool(
                    conn.execute(
                        """
                        SELECT 1
                        FROM paper_position_applications AS aplicacion
                        JOIN paper_positions AS posicion
                            ON posicion.id = aplicacion.position_id
                        WHERE posicion.mint = ?
                        AND posicion.status = 'closed'
                        AND aplicacion.event_id = ?
                        LIMIT 1
                        """,
                        (mint, event_id),
                    ).fetchone()
                )

            conn.rollback()
            return {
                "applied": False,
                "needs_untrack": cerrada_por_este_evento,
            }

        position_id = position[0]

        if event_id is not None:
            ya_aplicado = conn.execute(
                """
                SELECT 1
                FROM paper_position_applications
                WHERE position_id = ?
                AND event_id = ?
                LIMIT 1
                """,
                (position_id, event_id),
            ).fetchone()

            if ya_aplicado:
                conn.rollback()
                # La posición sigue abierta, así que no hay limpieza pendiente.
                return {"applied": False, "needs_untrack": False}

        # Una operación anterior a la entrada no pertenece a esta posición.
        #
        # Con webhooks atrasados, un evento viejo puede llegar después de que
        # se abrió una posición nueva sobre el mismo token y moverla como si
        # fuera actual. La referencia es on-chain contra on-chain: `opened_ts`
        # no sirve, porque mide cuándo reaccionamos nosotros y es posterior al
        # evento que nos hizo entrar, así que rechazaría operaciones
        # legítimamente posteriores a la entrada.
        #
        # La igualdad se acepta: el timestamp on-chain tiene resolución de
        # segundos, así que dos transacciones distintas del mismo segundo son
        # indistinguibles y descartarlas sería perder operaciones válidas.
        #
        # Si falta cualquiera de los dos lados, la guarda se apaga y queda el
        # comportamiento previo. Inventar una referencia sería peor que no
        # tenerla.
        entrada_ts = stored_block_event_ts(position[7])
        ultimo_evento_ts = (
            stored_block_event_ts(position[8]) or entrada_ts
        )

        if (
            event_block_event_ts is not None
            and entrada_ts is not None
            and event_block_event_ts < entrada_ts
        ):
            # Se deja marcado como aplicado para que un reintento no lo vuelva
            # a evaluar para siempre. La marca dice que se lo ignoró y por qué.
            if event_id is not None:
                conn.execute(
                    """
                    INSERT INTO paper_position_applications(
                        position_id, event_id, applied_ts, action
                    )
                    VALUES(?,?,?,?)
                    """,
                    (position_id, event_id, time.time(), "IGNORED_PRE_ENTRY"),
                )
                conn.commit()
            else:
                conn.rollback()

            return {"applied": False, "needs_untrack": False}

        if (
            event_block_event_ts is not None
            and ultimo_evento_ts is not None
            and event_block_event_ts < ultimo_evento_ts
        ):
            if event_id is not None:
                conn.execute(
                    """
                    INSERT INTO paper_position_applications(
                        position_id, event_id, applied_ts, action
                    )
                    VALUES(?,?,?,?)
                    """,
                    (
                        position_id,
                        event_id,
                        time.time(),
                        "IGNORED_OUT_OF_ORDER",
                    ),
                )
                conn.commit()
            else:
                conn.rollback()

            return {"applied": False, "needs_untrack": False}

        entry_mc = float(position[1] or 0)
        stake_usd = float(position[2] or 0)
        remaining = float(position[3] or 0)
        realized = float(position[4] or 0)
        origin_trader = position[5] or ""
        tp_stage = int(position[6] or 0)

        if entry_mc <= 0 or remaining <= 0:
            conn.rollback()
            return {"applied": False, "needs_untrack": False}

        change_pct = market_cap / entry_mc - 1

        decision = decide_paper_position_action(
            change_pct=change_pct,
            remaining=remaining,
            tp_stage=tp_stage,
            side=side,
            trader=trader,
            origin_trader=origin_trader,
            new_token_balance=new_token_balance,
        )

        action = decision["action"]
        sell_fraction = decision["sell_fraction"]
        exit_reason = decision["exit_reason"]
        tp_stage = decision["tp_stage"]

        if sell_fraction > 0:
            realized += stake_usd * sell_fraction * change_pct
            remaining = max(0, remaining - sell_fraction)

        unrealized = stake_usd * remaining * change_pct
        total_pnl = realized + unrealized

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
                exit_reason = ?,
                last_applied_block_event_ts = CASE
                    WHEN ? IS NULL THEN last_applied_block_event_ts
                    ELSE ?
                END
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
                event_block_event_ts,
                event_block_event_ts,
                position_id,
            ),
        )

        # La auditoría va en la misma transacción que el efecto. Si quedara
        # afuera y fallara, el reintento vería la marca de aplicación y el
        # evento de auditoría se perdería para siempre.
        if action != "HOLD":
            save_position_event(
                mint=mint,
                event_type=action,
                market_cap=market_cap,
                remaining_pct=remaining * 100,
                realized_pnl_usd=realized,
                details=exit_reason,
                connection=conn,
            )

        # La marca, también en la misma transacción: si el proceso muere entre
        # el efecto y la marca, el reintento volvería a aplicar el efecto.
        if event_id is not None:
            conn.execute(
                """
                INSERT INTO paper_position_applications(
                    position_id, event_id, applied_ts, action
                )
                VALUES(?,?,?,?)
                """,
                (position_id, event_id, time.time(), action),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "applied": True,
        "needs_untrack": status == "closed",
        "action": action,
        "change_pct": change_pct,
        "remaining": remaining,
        "total_pnl": total_pnl,
    }


def update_paper_position(
    mint,
    trader,
    side,
    market_cap,
    new_token_balance,
    event_signature=None,
    event_index=0,
    event_block_event_ts=None,
):
    """Aplica un evento de mercado a la posición paper abierta de ``mint``.

    Con ``event_signature`` la aplicación es idempotente: el mismo evento
    sobre la misma posición se aplica una sola vez aunque el llamador
    reintente. La identidad incluye el índice del evento dentro de la
    transacción, porque una transacción puede traer varias operaciones.

    Sin firma se conserva el comportamiento anterior, para las rutas de demo
    que no tienen una identidad que ofrecer.
    """

    if not mint or market_cap <= 0:
        return

    event_id = market_event_identity(event_signature, event_index)
    event_block_event_ts = validated_block_event_ts(event_block_event_ts)

    resultado = apply_paper_event(
        mint=mint,
        trader=trader,
        side=side,
        market_cap=market_cap,
        new_token_balance=new_token_balance,
        event_id=event_id,
        event_block_event_ts=event_block_event_ts,
    )

    # Fuera de la transacción: abre su propia conexión y se bloquearía contra el
    # lock de escritura que sostenía la anterior. Va antes del corte por
    # `applied` porque un reintento sobre una posición ya cerrada no aplica
    # nada, pero sí puede tener limpieza pendiente de un intento que falló acá.
    if resultado["needs_untrack"]:
        untrack_token_if_unused(mint)

    if not resultado["applied"]:
        return

    print(
        f"[POSITION] {resultado['action']} | "
        f"{mint} | "
        f"{resultado['change_pct'] * 100:+.1f}% | "
        f"restante {resultado['remaining'] * 100:.0f}% | "
        f"PnL ${resultado['total_pnl']:+.2f}"
    )



def save_trade(
    trader,
    wallet,
    event,
    source="live",
    allow_live_buys=True,
    allow_live_exits=True,
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

    # La otra mitad de la identidad del evento. Se lee acá, al lado de la firma,
    # para que no puedan separarse.
    event_index = market_event_index(event)
    event_block_event_ts = market_event_block_ts(event)
    trade_ts = event_block_event_ts or time.time()


    token_amount = float(
        event.get("tokenAmount")
        or 0
    )

    v_sol = float(
        event.get("vSolInBondingCurve")
        or 0
    )

    v_tokens = float(
        event.get("vTokensInBondingCurve")
        or 0
    )

    if v_sol > 0 and v_tokens > 0:
        price_at_signal = v_sol / v_tokens
    elif market_cap > 0:
        price_at_signal = (
            market_cap / PUMP_TOKEN_SUPPLY
        )
    else:
        price_at_signal = 0.0


    event_new_token_balance = market_event_new_token_balance(event)
    paper_new_token_balance = (
        event_new_token_balance
        if "newTokenBalance" in event
        else 0.0
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
            trade_ts,

            trader,

            wallet,

            side,

            mint,

            sol,

            market_cap,

            signature,

            source,

            token_amount,

            paper_new_token_balance,

            pool
        )
    )


    if source == "live" and wallet:
        conn.execute(
            """
            INSERT INTO watched_wallet_activity(
                wallet,
                trader,
                last_event_ts,
                events
            )
            VALUES(?,?,?,1)
            ON CONFLICT(wallet) DO UPDATE SET
                trader = excluded.trader,
                last_event_ts = excluded.last_event_ts,
                events = watched_wallet_activity.events + 1
            """,
            (
                wallet,
                trader,
                time.time(),
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
        source=source,
        event_index=event_index,
        event_ts=event_block_event_ts,
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
            source,
            price_at_signal,
            allow_live_buys=allow_live_buys,
        )
    

    # Actualizar cualquier posición paper
    # abierta en este token.
    update_paper_position(
        mint=mint,
        trader=trader,
        side=side,
        market_cap=market_cap,
        new_token_balance=paper_new_token_balance,
        event_signature=signature,
        event_index=event_index,
        event_block_event_ts=event_block_event_ts,
    )
    if allow_live_exits:
        evaluate_live_position_exit(
            mint=mint,
            trader=trader,
            side=side,
            market_cap=market_cap,
            new_token_balance=event_new_token_balance,
            event_signature=signature,
            event_index=event_index,
            event_block_event_ts=event_block_event_ts,
        )
# =========================================================
# STREAM REAL PUMPPORTAL
# =========================================================

PUMPPORTAL_ERROR_HINTS = (
    "error",
    "failed",
    "invalid",
    "unauthorized",
    "forbidden",
    "funded",
    "balance",
    "rate limit",
    "banned",
    "only available",
)


def is_pumpportal_error_message(message):
    normalized = str(message or "").strip().lower()

    return any(
        hint in normalized
        for hint in PUMPPORTAL_ERROR_HINTS
    )


def post_discord_alert(message):
    if not DISCORD_ALERT_WEBHOOK_URL:
        return False

    body = json.dumps(
        {"content": str(message)[:1900]}
    ).encode("utf-8")

    request = Request(
        DISCORD_ALERT_WEBHOOK_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Pump-Copilot/1.0",
        },
        method="POST",
    )

    with urlopen(request, timeout=10) as response:
        return 200 <= response.status < 300


async def send_discord_alert(message):
    try:
        sent = await asyncio.to_thread(
            post_discord_alert,
            message,
        )

        if sent:
            print("[ALERT] Discord notification sent")

        return bool(sent)

    except Exception as ex:
        print("[ALERT ERROR]", repr(ex))
        return False


async def maybe_send_shadow_review_alert():
    if not DISCORD_ALERT_WEBHOOK_URL:
        return False

    stats = get_shadow_stats()
    assessment = stats["promotion_assessment"]
    comparison = stats.get("comparison")
    challenger_version = stats.get("challenger_model_version")

    if (
        not assessment["ready_for_review"]
        or not comparison
        or not challenger_version
    ):
        return False

    state_key = (
        f"SHADOW_REVIEW_ALERT:{challenger_version}:"
        f"{assessment['minimum_completed']}"
    )
    conn = db()
    already_sent = conn.execute(
        "SELECT 1 FROM app_state WHERE key = ? LIMIT 1",
        (state_key,),
    ).fetchone()
    conn.close()

    if already_sent:
        return False

    incumbent = comparison["incumbent_metrics"]
    challenger = comparison["challenger_metrics"]

    def percent(value):
        return (
            f"{float(value) * 100:.1f}%"
            if value is not None
            else "n/a"
        )

    sent = await send_discord_alert(
        "Pump Copilot: shadow comparison ready for review.\n"
        f"Paired completed: {comparison['completed']}\n"
        f"Leader: {assessment['leader']}\n"
        f"Incumbent precision/recall: "
        f"{percent(incumbent['precision'])} / "
        f"{percent(incumbent['recall'])}\n"
        f"Challenger precision/recall: "
        f"{percent(challenger['precision'])} / "
        f"{percent(challenger['recall'])}\n"
        f"Exclusive correct: incumbent "
        f"{comparison['incumbent_only_correct']}, challenger "
        f"{comparison['challenger_only_correct']}\n"
        "No model was promoted automatically."
    )

    if not sent:
        return False

    conn = db()
    conn.execute(
        """
        INSERT OR IGNORE INTO app_state(key, value)
        VALUES(?, ?)
        """,
        (state_key, str(time.time())),
    )
    conn.commit()
    conn.close()
    return True


def fetch_solana_balance_sol(wallet_address):
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [
                wallet_address,
                {"commitment": "confirmed"},
            ],
        }
    ).encode("utf-8")

    request = Request(
        SOLANA_RPC_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Pump-Copilot/1.0",
        },
        method="POST",
    )

    with urlopen(request, timeout=15) as response:
        payload = json.load(response)

    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))

    lamports = payload.get("result", {}).get("value")

    if lamports is None:
        raise RuntimeError("Solana RPC response has no balance")

    return float(lamports) / 1_000_000_000


def fetch_solana_signature_status(signature, timeout_seconds=5):
    signature = normalize_solana_signature(signature)

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [
            [signature],
            {"searchTransactionHistory": True},
        ],
    }).encode("utf-8")
    request = Request(
        SOLANA_RPC_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Pump-Copilot/1.0",
        },
        method="POST",
    )

    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if payload.get("error"):
        raise ValueError("SOLANA_RPC_ERROR")

    try:
        status = payload["result"]["value"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("INVALID_SOLANA_RPC_RESPONSE") from exc

    if status is None:
        return {
            "found": False,
            "confirmed": False,
            "finalized": False,
            "failed": False,
            "confirmation_status": None,
            "slot": None,
            "error": None,
        }

    confirmation_status = status.get("confirmationStatus")
    error = status.get("err")
    return {
        "found": True,
        "confirmed": (
            error is None
            and confirmation_status in ("confirmed", "finalized")
        ),
        "finalized": error is None and confirmation_status == "finalized",
        "failed": error is not None,
        "confirmation_status": confirmation_status,
        "slot": status.get("slot"),
        "error": error,
    }


async def record_pumpportal_wallet_balance(balance_sol):
    global PUMPPORTAL_WALLET_BALANCE_SOL
    global PUMPPORTAL_BALANCE_CHECKED_TS
    global PUMPPORTAL_BALANCE_LAST_ERROR
    global PUMPPORTAL_BALANCE_ALERT_ACTIVE

    balance_sol = float(balance_sol)
    was_low = PUMPPORTAL_BALANCE_ALERT_ACTIVE
    is_low = balance_sol < PUMPPORTAL_LOW_BALANCE_SOL

    PUMPPORTAL_WALLET_BALANCE_SOL = balance_sol
    PUMPPORTAL_BALANCE_CHECKED_TS = time.time()
    PUMPPORTAL_BALANCE_LAST_ERROR = ""
    PUMPPORTAL_BALANCE_ALERT_ACTIVE = is_low

    if is_low and not was_low:
        await send_discord_alert(
            "Pump Copilot: PumpPortal wallet balance is low.\n"
            f"Balance: {balance_sol:.6f} SOL\n"
            f"Warning threshold: {PUMPPORTAL_LOW_BALANCE_SOL:.6f} SOL\n"
            "Top up the API wallet before data collection stops."
        )
    elif not is_low and was_low:
        await send_discord_alert(
            "Pump Copilot: PumpPortal wallet balance recovered.\n"
            f"Balance: {balance_sol:.6f} SOL"
        )


async def pumpportal_balance_monitor():
    global PUMPPORTAL_BALANCE_CHECKED_TS
    global PUMPPORTAL_BALANCE_LAST_ERROR

    while True:
        try:
            balance_sol = await asyncio.to_thread(
                fetch_solana_balance_sol,
                PUMPPORTAL_WALLET_ADDRESS,
            )
            await record_pumpportal_wallet_balance(balance_sol)
        except Exception as ex:
            PUMPPORTAL_BALANCE_CHECKED_TS = time.time()
            PUMPPORTAL_BALANCE_LAST_ERROR = str(ex)[:500]
            print("[BALANCE MONITOR ERROR]", repr(ex))

        await asyncio.sleep(PUMPPORTAL_BALANCE_CHECK_SECONDS)


def get_watched_wallet_activity():
    """Estado de entrega de cada wallet vigilada.

    Una wallet puede dejar de entregar eventos mientras el stream sigue
    conectado y otras wallets siguen llegando. Sin esta vista, ese caso es
    indistinguible de que el trader simplemente no esté operando.
    """
    conn = db()

    rows = conn.execute(
        """
        SELECT wallet, trader, last_event_ts, events
        FROM watched_wallet_activity
        """
    ).fetchall()

    conn.close()

    by_wallet = {
        str(row[0]): {
            "trader": row[1],
            "last_event_ts": float(row[2] or 0),
            "events": int(row[3] or 0),
        }
        for row in rows
    }

    now = time.time()
    result = []

    for trader, wallet in WATCHED.items():
        record = by_wallet.get(wallet)
        last_event_ts = record["last_event_ts"] if record else None
        age = (
            now - last_event_ts
            if last_event_ts
            else None
        )

        result.append({
            "trader": trader,
            "wallet": wallet,
            "events": record["events"] if record else 0,
            "last_event_ts": last_event_ts,
            "age_seconds": age,
            "age_hours": round(age / 3600, 2) if age is not None else None,
            "never_seen": last_event_ts is None,
            "silent": (
                age is None
                or age >= WATCHED_WALLET_SILENCE_SECONDS
            ),
        })

    result.sort(
        key=lambda item: (
            item["last_event_ts"] is not None,
            item["last_event_ts"] or 0,
        )
    )

    return result


async def check_watched_wallet_silence():
    """Avisa cuando una wallet vigilada deja de entregar con el stream sano.

    Solo se evalúa con el stream conectado: si el stream está caído, la alerta
    correcta es la del stream y no una por cada wallet.
    """
    if not STREAM_CONNECTED:
        return []

    activity = get_watched_wallet_activity()
    newly_silent = []
    recovered = []

    for item in activity:
        wallet = item["wallet"]

        if item["silent"]:
            if wallet not in WATCHED_WALLET_ALERTS:
                WATCHED_WALLET_ALERTS.add(wallet)
                newly_silent.append(item)
        elif wallet in WATCHED_WALLET_ALERTS:
            WATCHED_WALLET_ALERTS.discard(wallet)
            recovered.append(item)

    if DISCORD_ALERT_WEBHOOK_URL and newly_silent:
        detail = "\n".join(
            (
                f"- @{item['trader']}: nunca entregó eventos"
                if item["never_seen"]
                else (
                    f"- @{item['trader']}: sin eventos hace "
                    f"{item['age_hours']:.1f}h"
                )
            )
            for item in newly_silent
        )

        await send_discord_alert(
            "Pump Copilot: wallets vigiladas sin entregar datos "
            "(el stream sigue conectado).\n"
            f"{detail}\n"
            "Revisar la suscripción de esas cuentas en PumpPortal."
        )

    if DISCORD_ALERT_WEBHOOK_URL and recovered:
        detail = ", ".join(f"@{item['trader']}" for item in recovered)

        await send_discord_alert(
            f"Pump Copilot: volvieron a entregar datos: {detail}"
        )

    return newly_silent


async def watched_wallet_monitor():
    while True:
        try:
            await check_watched_wallet_silence()
        except Exception as ex:
            print("[WALLET MONITOR ERROR]", repr(ex))

        await asyncio.sleep(WATCHED_WALLET_CHECK_SECONDS)


def get_rpc_fallback_wallet_states():
    conn = db()
    rows = conn.execute(
        """
        SELECT
            wallet, trader, last_signature, last_slot,
            last_polled_ts, baseline_ts, last_error
        FROM rpc_fallback_wallet_state
        """
    ).fetchall()
    conn.close()
    return {
        row[0]: {
            "wallet": row[0],
            "trader": row[1],
            "last_signature": row[2],
            "last_slot": row[3],
            "last_polled_ts": row[4],
            "baseline_ts": row[5],
            "last_error": row[6] or "",
        }
        for row in rows
    }


def update_rpc_fallback_wallet_state(
    wallet,
    trader,
    last_signature=None,
    last_slot=None,
    last_error="",
):
    conn = db()
    conn.execute(
        """
        INSERT INTO rpc_fallback_wallet_state(
            wallet, trader, last_signature, last_slot,
            last_polled_ts, baseline_ts, last_error
        )
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(wallet) DO UPDATE SET
            trader = excluded.trader,
            last_signature = COALESCE(
                excluded.last_signature,
                rpc_fallback_wallet_state.last_signature
            ),
            last_slot = COALESCE(
                excluded.last_slot,
                rpc_fallback_wallet_state.last_slot
            ),
            last_polled_ts = excluded.last_polled_ts,
            baseline_ts = COALESCE(rpc_fallback_wallet_state.baseline_ts, excluded.baseline_ts),
            last_error = excluded.last_error
        """,
        (
            wallet,
            trader,
            last_signature,
            last_slot,
            time.time(),
            time.time(),
            str(last_error or "")[:500],
        ),
    )
    conn.commit()
    conn.close()


def record_rpc_fallback_event(trader, wallet, receipt, parsed):
    event = parsed["event"]
    signature = event["signature"]
    event_index = market_event_index(event, required=True)
    conn = db()
    # La referencia es la reserva de identidad, no `trades`: tiene el índice
    # —`trades` no— y también cubre lo que aplicó el consumidor de Helius. Lo
    # que el fallback busca es lo que ningún transporte aplicó; con firma sola,
    # la segunda operación de una misma transacción quedaba oculta detrás de
    # la primera.
    applied_row = conn.execute(
        """
        SELECT 1 FROM processed_market_events
        WHERE signature = ? AND event_index = ?
        LIMIT 1
        """,
        (signature, event_index),
    ).fetchone()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO rpc_fallback_events(
            signature, event_index, slot, block_time, detected_ts,
            trader, wallet, program, event_name, side, mint, sol,
            market_cap_sol, token_amount, new_token_balance, pool,
            event_json, status
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            signature,
            # Del evento, que es lo que se serializa abajo en `event_json`: así
            # la columna y el JSON no pueden discrepar. `required` porque acá el
            # evento es nuestro: si no trae índice, se perdió en el camino.
            event_index,
            receipt.get("slot"),
            receipt.get("blockTime"),
            time.time(),
            trader,
            wallet,
            parsed["program"],
            parsed["event_name"],
            event["txType"],
            event["mint"],
            float(event["solAmount"]),
            float(event["marketCapSol"]),
            float(event["tokenAmount"]),
            # ``None`` es desconocido y se guarda como NULL; un valor presente
            # pero inservible es un bug del parser y conviene que estalle.
            market_event_new_token_balance(event),
            event["pool"],
            json.dumps(event, separators=(",", ":"), sort_keys=True),
            "matched" if applied_row else "pending",
        ),
    )
    conn.commit()
    inserted = cursor.rowcount > 0
    conn.close()
    return inserted


def reconcile_rpc_fallback_events(now=None):
    now = float(now if now is not None else time.time())
    conn = db()
    conn.execute(
        """
        UPDATE rpc_fallback_events
        SET status = 'discarded_prebaseline'
        WHERE status IN ('pending', 'missing')
          AND block_time IS NOT NULL
          AND block_time < COALESCE((
              SELECT baseline_ts FROM rpc_fallback_wallet_state
              WHERE wallet = rpc_fallback_events.wallet
          ), 0)
        """
    )
    conn.execute(
        """
        UPDATE rpc_fallback_events
        SET status = 'matched'
        WHERE status NOT IN ('matched', 'discarded_prebaseline')
        AND EXISTS(
            SELECT 1 FROM processed_market_events AS applied
            WHERE applied.signature = rpc_fallback_events.signature
            AND applied.event_index = rpc_fallback_events.event_index
        )
        """
    )
    conn.execute(
        """
        UPDATE rpc_fallback_events
        SET status = 'missing'
        WHERE status = 'pending'
        AND detected_ts <= ?
        AND NOT EXISTS(
            SELECT 1 FROM processed_market_events AS applied
            WHERE applied.signature = rpc_fallback_events.signature
            AND applied.event_index = rpc_fallback_events.event_index
        )
        """,
        (now - RPC_FALLBACK_GRACE_SECONDS,),
    )
    rows = conn.execute(
        """
        SELECT id, trader, wallet, signature, side, mint, pool, block_time
        FROM rpc_fallback_events
        WHERE status = 'missing'
        AND alerted_ts IS NULL
        ORDER BY detected_ts
        LIMIT 20
        """
    ).fetchall()
    conn.commit()
    conn.close()
    return [
        {
            "id": row[0],
            "trader": row[1],
            "wallet": row[2],
            "signature": row[3],
            "side": row[4],
            "mint": row[5],
            "pool": row[6],
            "block_time": row[7],
        }
        for row in rows
    ]


def mark_rpc_fallback_events_alerted(event_ids):
    clean_ids = [int(event_id) for event_id in event_ids]
    if not clean_ids:
        return
    placeholders = ",".join("?" for _ in clean_ids)
    conn = db()
    conn.execute(
        f"UPDATE rpc_fallback_events SET alerted_ts = ? "
        f"WHERE id IN ({placeholders})",
        (time.time(), *clean_ids),
    )
    conn.commit()
    conn.close()


def poll_rpc_fallback_once():
    global RPC_FALLBACK_LAST_POLL_TS
    global RPC_FALLBACK_LAST_SUCCESS_TS
    global RPC_FALLBACK_LAST_ERROR
    global RPC_FALLBACK_SCANNED_SIGNATURES
    global RPC_FALLBACK_PARSED_EVENTS
    global RPC_FALLBACK_SATURATED_WALLETS

    RPC_FALLBACK_LAST_POLL_TS = time.time()
    states = get_rpc_fallback_wallet_states()
    queues = {}
    errors = []
    saturated = []
    successful_queries = 0

    for trader, wallet in WATCHED.items():
        state = states.get(wallet) or {}
        last_signature = state.get("last_signature")
        state_exists = bool(last_signature)
        try:
            rows = fetch_signatures_for_address(
                SOLANA_RPC_URL,
                wallet,
                limit=(1 if not state_exists else RPC_FALLBACK_SIGNATURE_LIMIT),
                until=last_signature,
            )
        except Exception as ex:
            error = str(ex or ex.__class__.__name__)[:500]
            errors.append(f"{trader}: {error}")
            update_rpc_fallback_wallet_state(
                wallet, trader, last_error=error
            )
            continue

        successful_queries += 1

        # El primer ciclo fija un punto de partida actual. No intenta reconstruir
        # historia parcial, porque una sola página no garantiza que esté completa.
        if not state_exists:
            if rows:
                update_rpc_fallback_wallet_state(
                    wallet,
                    trader,
                    last_signature=str(rows[0]["signature"]),
                    last_slot=rows[0].get("slot"),
                )
            else:
                update_rpc_fallback_wallet_state(wallet, trader)
            continue

        if (
            len(rows) >= RPC_FALLBACK_SIGNATURE_LIMIT
            and RPC_FALLBACK_SIGNATURE_LIMIT < 1000
        ):
            try:
                rows = fetch_signatures_for_address(
                    SOLANA_RPC_URL,
                    wallet,
                    limit=1000,
                    until=last_signature,
                )
            except Exception as ex:
                error = str(ex or ex.__class__.__name__)[:500]
                errors.append(f"{trader}: {error}")
                update_rpc_fallback_wallet_state(
                    wallet, trader, last_error=error
                )
                continue

        if len(rows) >= 1000:
            saturated.append(trader)
            error = "SIGNATURE_BACKLOG_REBASED"
            errors.append(f"{trader}: {error}")
            update_rpc_fallback_wallet_state(
                wallet,
                trader,
                last_signature=str(rows[0]["signature"]),
                last_slot=rows[0].get("slot"),
                last_error=error,
            )
            continue

        queues[wallet] = {
            "trader": trader,
            "rows": collections.deque(reversed(rows)),
            "blocked": False,
        }
        update_rpc_fallback_wallet_state(wallet, trader)

    processed_transactions = 0
    while processed_transactions < RPC_FALLBACK_MAX_TRANSACTIONS_PER_POLL:
        made_progress = False
        for wallet, queue in queues.items():
            if queue["blocked"] or not queue["rows"]:
                continue
            made_progress = True
            row = queue["rows"].popleft()
            signature = str(row["signature"])
            slot = row.get("slot")

            if row.get("err") is not None:
                update_rpc_fallback_wallet_state(
                    wallet,
                    queue["trader"],
                    last_signature=signature,
                    last_slot=slot,
                )
                RPC_FALLBACK_SCANNED_SIGNATURES += 1
                continue

            try:
                receipt = fetch_confirmed_transaction(
                    SOLANA_RPC_URL,
                    signature,
                )
                if receipt is None:
                    raise ValueError("TRANSACTION_NOT_AVAILABLE")
                parsed_events = parse_watched_wallet_pump_events(
                    receipt,
                    wallet,
                    signature,
                )
            except Exception as ex:
                error = str(ex or ex.__class__.__name__)[:500]
                errors.append(f"{queue['trader']}: {error}")
                update_rpc_fallback_wallet_state(
                    wallet,
                    queue["trader"],
                    last_error=error,
                )
                queue["blocked"] = True
                continue

            for parsed in parsed_events:
                if record_rpc_fallback_event(
                    queue["trader"], wallet, receipt, parsed
                ):
                    RPC_FALLBACK_PARSED_EVENTS += 1

            update_rpc_fallback_wallet_state(
                wallet,
                queue["trader"],
                last_signature=signature,
                last_slot=slot,
            )
            RPC_FALLBACK_SCANNED_SIGNATURES += 1
            processed_transactions += 1
            if processed_transactions >= RPC_FALLBACK_MAX_TRANSACTIONS_PER_POLL:
                break

        if not made_progress:
            break

    RPC_FALLBACK_SATURATED_WALLETS = saturated
    if successful_queries:
        RPC_FALLBACK_LAST_SUCCESS_TS = time.time()
    RPC_FALLBACK_LAST_ERROR = "; ".join(errors[:5])
    return {
        "wallets_queried": successful_queries,
        "transactions_processed": processed_transactions,
        "errors": errors,
        "saturated_wallets": saturated,
    }


async def rpc_fallback_shadow_worker():
    global RPC_FALLBACK_LAST_ERROR

    while True:
        try:
            await asyncio.to_thread(poll_rpc_fallback_once)
            missing = await asyncio.to_thread(reconcile_rpc_fallback_events)
            await update_helius_webhook_delivery_alert()
            if DISCORD_ALERT_WEBHOOK_URL and missing:
                detail = "\n".join(
                    f"- @{item['trader']}: {item['side']} "
                    f"{item['mint'][:8]}... ({item['pool']})"
                    for item in missing
                )
                sent = await send_discord_alert(
                    "Pump Copilot: el monitor RPC detectó operaciones Pump "
                    "que PumpPortal no entregó.\n"
                    f"{detail}\n"
                    "Modo observacional: no generaron señales ni órdenes."
                )
                if sent:
                    await asyncio.to_thread(
                        mark_rpc_fallback_events_alerted,
                        [item["id"] for item in missing],
                    )
        except Exception as ex:
            RPC_FALLBACK_LAST_ERROR = str(ex or ex.__class__.__name__)[:500]
            print("[RPC FALLBACK ERROR]", repr(ex))

        await asyncio.sleep(RPC_FALLBACK_POLL_SECONDS)


def has_recent_rpc_pump_activity(now=None, connection=None):
    current_ts = float(now if now is not None else time.time())
    owns_connection = connection is None
    conn = connection or db()
    try:
        row = conn.execute(
            "SELECT 1 FROM rpc_fallback_events "
            "WHERE block_time >= ? AND block_time <= ? LIMIT 1",
            (current_ts - 1800, current_ts),
        ).fetchone()
        return row is not None
    finally:
        if owns_connection:
            conn.close()


def get_helius_webhook_delivery_alert_active(connection=None):
    owns_connection = connection is None
    conn = connection or db()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            ("HELIUS_WEBHOOK_DELIVERY_ALERT_ACTIVE",),
        ).fetchone()
        return bool(row and row[0] == "1")
    finally:
        if owns_connection:
            conn.close()


def set_helius_webhook_delivery_alert_active(active):
    conn = db()
    try:
        conn.execute(
            "INSERT INTO app_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("HELIUS_WEBHOOK_DELIVERY_ALERT_ACTIVE", "1" if active else "0"),
        )
        conn.commit()
    finally:
        conn.close()


async def update_helius_webhook_delivery_alert(now=None):
    if not DISCORD_ALERT_WEBHOOK_URL or not HELIUS_WEBHOOK_ENABLED:
        return False
    current_ts = float(now if now is not None else time.time())
    latest = await asyncio.to_thread(get_latest_helius_pump_event_received_ts)
    active = await asyncio.to_thread(get_helius_webhook_delivery_alert_active)
    fresh = latest is not None and current_ts - latest <= 1800
    if active and fresh:
        sent = await send_discord_alert(
            "Pump Copilot: Helius volvió a entregar operaciones Pump."
        )
        if sent:
            await asyncio.to_thread(
                set_helius_webhook_delivery_alert_active, False
            )
        return bool(sent)
    if active or fresh:
        return False
    rpc_active = await asyncio.to_thread(has_recent_rpc_pump_activity, current_ts)
    if not rpc_active:
        return False
    sent = await send_discord_alert(
        "Pump Copilot: Helius lleva más de 30 minutos sin entregar "
        "operaciones Pump, aunque el monitor RPC detectó actividad "
        "on-chain reciente. La sincronización de direcciones no prueba "
        "que el webhook esté entregando datos. No habilitar compras reales."
    )
    if sent:
        await asyncio.to_thread(
            set_helius_webhook_delivery_alert_active, True
        )
    return bool(sent)


def get_rpc_fallback_stats():
    conn = db()
    count_rows = conn.execute(
        """
        SELECT status, COUNT(*)
        FROM rpc_fallback_events
        GROUP BY status
        """
    ).fetchall()
    counts = {row[0]: int(row[1]) for row in count_rows}
    identity_matches = conn.execute(
        """
        SELECT COUNT(*)
        FROM rpc_fallback_events AS rpc
        JOIN processed_market_events AS stream
          ON stream.signature = rpc.signature
         AND stream.event_index = rpc.event_index
        WHERE rpc.status = 'matched'
        AND stream.source = 'live'
        """
    ).fetchone()[0]
    # DISTINCT por evento: con una tolerancia de ±300s una misma observación
    # puede emparejar con varias operaciones del stream sobre el mismo token,
    # y el JOIN las contaría una vez por coincidencia. Lo que se quiere medir
    # es cuántos eventos tienen equivalente aproximado, no cuántos pares hay.
    approximate_matches = conn.execute(
        """
        SELECT COUNT(DISTINCT rpc.id) FROM rpc_fallback_events AS rpc
        JOIN trades AS stream ON stream.wallet = rpc.wallet
          AND stream.mint = rpc.mint
          AND LOWER(stream.side) = LOWER(rpc.side)
          AND ABS(stream.ts - rpc.block_time) <= 300
        WHERE rpc.status = 'missing'
        """
    ).fetchone()[0]
    missing_rows = conn.execute(
        """
        SELECT trader, signature, side, mint, pool, block_time, detected_ts,
               EXISTS(SELECT 1 FROM trades AS stream
                      WHERE stream.wallet = rpc_fallback_events.wallet
                        AND stream.mint = rpc_fallback_events.mint
                        AND LOWER(stream.side) = LOWER(rpc_fallback_events.side)
                        AND ABS(stream.ts - rpc_fallback_events.block_time) <= 300)
        FROM rpc_fallback_events
        WHERE status = 'missing'
        ORDER BY detected_ts DESC
        LIMIT 20
        """
    ).fetchall()
    state_rows = conn.execute(
        """
        SELECT trader, wallet, last_signature, last_slot,
               last_polled_ts, baseline_ts, last_error
        FROM rpc_fallback_wallet_state
        ORDER BY trader COLLATE NOCASE
        """
    ).fetchall()
    conn.close()

    matched = counts.get("matched", 0)
    return {
        "enabled": bool(RPC_FALLBACK_SHADOW_ENABLED),
        "observational": True,
        "affects_decisions": False,
        "poll_seconds": RPC_FALLBACK_POLL_SECONDS,
        "grace_seconds": RPC_FALLBACK_GRACE_SECONDS,
        "last_poll_ts": RPC_FALLBACK_LAST_POLL_TS or None,
        "last_success_ts": RPC_FALLBACK_LAST_SUCCESS_TS or None,
        "last_error": RPC_FALLBACK_LAST_ERROR or None,
        "scanned_signatures": RPC_FALLBACK_SCANNED_SIGNATURES,
        "parsed_events": RPC_FALLBACK_PARSED_EVENTS,
        "saturated_wallets": list(RPC_FALLBACK_SATURATED_WALLETS),
        "total": sum(counts.values()),
        "pending": counts.get("pending", 0),
        "matched": matched,
        "missing": counts.get("missing", 0),
        "matched_identity": int(identity_matches or 0),
        "approximate_matches": int(approximate_matches or 0),
        "identity_match_rate": (
            round(identity_matches / matched, 6) if matched else None
        ),
        "missing_events": [
            {
                "trader": row[0],
                "signature": row[1],
                "side": row[2],
                "mint": row[3],
                "pool": row[4],
                "block_time": row[5],
                "detected_ts": row[6],
                "approximate_match": bool(row[7]),
            }
            for row in missing_rows
        ],
        "wallets": [
            {
                "trader": row[0],
                "wallet": row[1],
                "last_signature": row[2],
                "last_slot": row[3],
                "last_polled_ts": row[4],
                "baseline_ts": row[5],
                "last_error": row[6] or None,
            }
            for row in state_rows
        ],
    }


async def mark_stream_problem(reason, immediate=False):
    global STREAM_CONNECTED
    global STREAM_LAST_ERROR
    global STREAM_ALERT_ACTIVE
    global STREAM_FAILURE_STARTED_TS

    now = time.time()
    STREAM_CONNECTED = False
    STREAM_LAST_ERROR = str(reason or "Unknown stream error")[:500]

    if STREAM_FAILURE_STARTED_TS <= 0:
        STREAM_FAILURE_STARTED_TS = now

    failure_age = now - STREAM_FAILURE_STARTED_TS

    if (
        DISCORD_ALERT_WEBHOOK_URL
        and not STREAM_ALERT_ACTIVE
        and (immediate or failure_age >= 180)
    ):
        STREAM_ALERT_ACTIVE = True
        await send_discord_alert(
            "Pump Copilot: PumpPortal stopped delivering data.\n"
            f"Reason: {STREAM_LAST_ERROR}\n"
            "Railway will continue reconnecting automatically."
        )


async def mark_stream_recovered():
    global STREAM_CONNECTED
    global STREAM_LAST_ERROR
    global STREAM_ALERT_ACTIVE
    global STREAM_FAILURE_STARTED_TS

    was_alerting = STREAM_ALERT_ACTIVE

    STREAM_CONNECTED = True
    STREAM_LAST_ERROR = ""
    STREAM_ALERT_ACTIVE = False
    STREAM_FAILURE_STARTED_TS = 0.0

    if DISCORD_ALERT_WEBHOOK_URL and was_alerting:
        await send_discord_alert(
            "Pump Copilot: PumpPortal data connection recovered."
        )

def route_market_event(event, allow_live_buys=True, allow_live_exits=True):
    """Apply an already deduplicated event using the existing live semantics.

    Transport authentication, deduplication and retry handling belong to the
    caller.

    Event identity travels inside the event: the caller sets ``eventIndex``
    when a transaction carries more than one operation, and this function
    forwards it alongside the signature. Transport permissions distinguish
    opening new live exposure from reducing existing exposure: Helius disables
    live buys while retaining live exits, and PumpPortal allows both.
    """
    wallet = (
        event.get("traderPublicKey")
        or event.get("user")
        or event.get("wallet")
        or ""
    )
    mint = event.get("mint") or ""
    is_watched_wallet = wallet in WATCHED.values()
    # Snapshot before save_trade: a buy may start tracking this mint.
    is_tracked_token = mint in TRACKED_TOKENS

    if is_watched_wallet:
        trader = trader_for(wallet)
        print(f"[TRADE LIVE] {trader}: {event}")
        save_trade(
            trader,
            wallet,
            event,
            source="live",
            allow_live_buys=allow_live_buys,
            allow_live_exits=allow_live_exits,
        )

    if not is_tracked_token:
        return

    print(f"[TOKEN LIVE] {mint}: {event}")
    process_signal_outcomes_event(mint=mint, event=event)
    # Watched-wallet history and positions were already updated by save_trade.
    if is_watched_wallet:
        return

    trader = trader_for(wallet)
    side = str(event.get("txType") or event.get("type") or "").lower()
    market_cap = float(
        event.get("marketCapSol") or event.get("market_cap_sol") or 0
    )
    signature = event.get("signature") or ""
    # La otra mitad de la identidad del evento, al lado de la firma.
    event_index = market_event_index(event)
    event_block_event_ts = market_event_block_ts(event)
    event_new_token_balance = market_event_new_token_balance(event)
    paper_new_token_balance = (
        event_new_token_balance
        if "newTokenBalance" in event
        else 0.0
    )
    save_token_history(
        mint=mint, market_cap=market_cap, trader=trader, side=side,
        signature=signature, source="token-live",
        event_index=event_index,
        event_ts=event_block_event_ts,
    )
    update_paper_position(
        mint=mint, trader=trader, side=side, market_cap=market_cap,
        new_token_balance=paper_new_token_balance,
        event_signature=signature,
        event_index=event_index,
        event_block_event_ts=event_block_event_ts,
    )
    if allow_live_exits:
        evaluate_live_position_exit(
            mint=mint, trader=trader, side=side, market_cap=market_cap,
            new_token_balance=event_new_token_balance,
            event_signature=signature,
            event_index=event_index,
            event_block_event_ts=event_block_event_ts,
        )


async def stream():

    global FORCE_STREAM_ERROR
    global STREAM_CONNECTED
    global LAST_STREAM_MESSAGE_TS
    global LAST_STREAM_EVENT_TS
    global LAST_PUMPPORTAL_MESSAGE

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
                
                # The socket opening is not proof that PumpPortal
                # accepted the subscriptions. A confirmation or a
                # real event marks the data stream as connected.
                STREAM_CONNECTED = False
                last_stream_message_ts = time.time()

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

                last_account_subscription_ts = time.time()


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

                        

                    # Reafirmar la suscripción de cuentas: si el proveedor la
                    # descartó en silencio, esto la recupera sin necesidad de
                    # que se caiga la conexión.
                    if (
                        time.time() - last_account_subscription_ts
                        >= WATCHED_RESUBSCRIBE_SECONDS
                    ):
                        await websocket.send(
                            json.dumps({
                                "method": "subscribeAccountTrade",
                                "keys": list(WATCHED.values())
                            })
                        )

                        last_account_subscription_ts = time.time()

                        print(
                            f"[STREAM] Suscripción de cuentas reafirmada "
                            f"({len(WATCHED)} wallets)"
                        )

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
                        inactivity = (
                            time.time()
                            - last_stream_message_ts
                        )

                        if inactivity >= STREAM_INACTIVITY_TIMEOUT:
                            print(
                                f"[STREAM WATCHDOG] "
                                f"Sin eventos por {int(inactivity)}s. "
                                f"Reconectando..."
                            )

                            raise RuntimeError(
                                "STREAM_INACTIVITY_TIMEOUT"
                            )

                        continue

                    else:
                        last_stream_message_ts = time.time()
                        LAST_STREAM_MESSAGE_TS = (
                            last_stream_message_ts
                        )
                        event = json.loads(raw)

                    if "message" in event:
                        provider_message = str(
                            event["message"] or ""
                        )
                        LAST_PUMPPORTAL_MESSAGE = (
                            provider_message[:500]
                        )

                        PUMPPORTAL_MESSAGE_LOG.append({
                            "ts": time.time(),
                            "message": provider_message[:500],
                        })

                        print(
                            "[PUMPPORTAL]",
                            provider_message
                        )

                        if is_pumpportal_error_message(
                            provider_message
                        ):
                            await mark_stream_problem(
                                provider_message,
                                immediate=True,
                            )
                            raise RuntimeError(
                                "PUMPPORTAL_SUBSCRIPTION_REJECTED"
                            )

                        await mark_stream_recovered()
                        continue

                    LAST_STREAM_EVENT_TS = time.time()
                    await mark_stream_recovered()

                    # =========================================================
                    # PROTECCIÓN CONTRA EVENTOS DUPLICADOS
                    # =========================================================

                    # `newTokenBalance` viene de afuera. Un valor inservible
                    # no puede tirar el stream ni perder el evento: pasa a
                    # desconocido, que paper y calidad ya saben tratar sin
                    # inventar una salida total.
                    if "newTokenBalance" in event:
                        try:
                            market_event_new_token_balance(event)
                        except ValueError:
                            print(
                                "[STREAM] newTokenBalance inservible, se "
                                "trata como desconocido: "
                                f"{event.get('newTokenBalance')!r}"
                            )
                            event["newTokenBalance"] = None

                    signature = event.get("signature")

                    if signature:
                        event_index = market_event_index(event)
                        event_id = market_event_identity(
                            signature,
                            event_index,
                        )

                        if event_id is None:
                            print("[STREAM] Evento ignorado: firma inválida")
                            continue

                        if event_id in SEEN_EVENT_IDS:
                            print(
                                f"[DUPLICATE MEMORY] Ignorado {event_id[:12]}..."
                            )
                            continue

                        if not mark_market_event_processed(
                            signature,
                            event_index=event_index,
                            source="live"
                        ):
                            print(
                                f"[DUPLICATE DB] Ignorado {event_id[:12]}..."
                            )
                            continue

                        SEEN_EVENT_IDS.add(event_id)

                        if len(SEEN_EVENT_IDS) > 5000:
                            SEEN_EVENT_IDS.clear()

                    route_market_event(event)


        except Exception as ex:
            STREAM_CONNECTED = False

            error_text = str(ex or ex.__class__.__name__)

            await mark_stream_problem(error_text)

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
    reconcile_finished_signal_outcomes()
    load_shadow_model()

    conn = db()

    stale_rows = conn.execute(
        """
        SELECT DISTINCT mint
        FROM signal_outcomes
        WHERE status = 'active'
        AND signal_ts < ?
        AND mint IS NOT NULL
        AND mint != ''
        """,
        (
            time.time() - 1200,
        )
    ).fetchall()

    # Recuperar tokens de posiciones paper abiertas
    paper_rows = conn.execute(
        """
        SELECT mint
        FROM paper_positions
        WHERE status = 'open'
        """
    ).fetchall()

    live_rows = conn.execute(
        """
        SELECT DISTINCT mint
        FROM live_positions
        WHERE status = 'open'
        AND mint IS NOT NULL
        AND mint != ''
        """
    ).fetchall()

    # Recuperar outcomes incompletos recientes
    outcome_rows = conn.execute(
        """
        SELECT DISTINCT mint
        FROM signal_outcomes
        WHERE mint IS NOT NULL
        AND mint != ''
        AND price_at_signal > 0
        AND (
            price_10s IS NULL
            OR price_30s IS NULL
            OR price_1m IS NULL
            OR price_5m IS NULL
            OR price_15m IS NULL
        )
        AND signal_ts >= ?
        """,
        (
            time.time() - 1200,
        )
    ).fetchall()

    conn.close()

    for row in stale_rows:
        mint = row[0]

        if mint:
            complete_finished_signal_outcomes(mint)
            expire_old_signal_outcomes(mint)

    for row in paper_rows:
        mint = row[0]

        if mint and not mint.startswith("DEMO"):
            TRACKED_TOKENS.add(mint)

    for row in live_rows:
        mint = row[0]

        if mint and not mint.startswith("DEMO"):
            TRACKED_TOKENS.add(mint)

    for row in outcome_rows:
        mint = row[0]

        if mint and not mint.startswith("DEMO"):
            TRACKED_TOKENS.add(mint)

    print(
        f"[TRACKER] {len(TRACKED_TOKENS)} "
        f"tokens recuperados"
    )

    asyncio.create_task(
        stream()
    )

    asyncio.create_task(
        pumpportal_balance_monitor()
    )

    asyncio.create_task(
        pumpportal_execution_reconciliation_worker()
    )

    asyncio.create_task(
        signal_outcome_checkpoint_worker()
    )

    asyncio.create_task(
        watched_wallet_monitor()
    )

    if RPC_FALLBACK_SHADOW_ENABLED:
        asyncio.create_task(
            rpc_fallback_shadow_worker()
        )

    if MARKET_EVENT_INBOX_VALIDATION_ENABLED:
        asyncio.create_task(
            market_event_inbox_validation_worker()
        )

    if MARKET_EVENT_INBOX_CONSUMER_ENABLED:
        activation_ts = establish_market_event_inbox_activation()
        print(
            "[HELIUS INBOX CONSUMER] Activado desde "
            f"{activation_ts:.3f}"
        )
        asyncio.create_task(
            market_event_inbox_consumer_worker()
        )

    if HELIUS_WEBHOOK_SYNC_ENABLED:
        asyncio.create_task(
            helius_webhook_sync_worker()
        )

    if HELIUS_STANDARD_WSS_ENABLED:
        asyncio.create_task(
            helius_standard_wss_worker()
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

def require_live_trading(execution_side=None):

    if not LIVE_TRADING:
        raise HTTPException(
            status_code=403,
            detail="LIVE_TRADING_DISABLED"
        )

    if not LIVE_EXECUTION_IMPLEMENTED:
        raise HTTPException(
            status_code=501,
            detail="LIVE_EXECUTION_NOT_IMPLEMENTED"
        )

    execution_side = str(
        execution_side or ""
    ).strip().lower()

    if execution_side == "buy" and not LIVE_BUYS_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="LIVE_BUYS_DISABLED"
        )

    if execution_side == "sell" and not LIVE_SELLS_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="LIVE_SELLS_DISABLED"
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


    stream_message_age_seconds = None

    if LAST_STREAM_MESSAGE_TS > 0:
        stream_message_age_seconds = round(
            max(
                0.0,
                time.time() - LAST_STREAM_MESSAGE_TS,
            ),
            3,
        )

    return {

        "ok":
            True,

        "version":
            "V3",

        "watched":
            WATCHED,

        "live_stream_configured":
            bool(API_KEY),

        "live_trading_requested":
            bool(LIVE_TRADING),

        "live_execution_implemented":
            bool(LIVE_EXECUTION_IMPLEMENTED),

        "live_buys_enabled":
            bool(LIVE_BUYS_ENABLED),

        "live_sells_enabled":
            bool(LIVE_SELLS_ENABLED),

        "live_trading_active":
            bool(
                LIVE_TRADING
                and LIVE_EXECUTION_IMPLEMENTED
                and (
                    LIVE_BUYS_ENABLED
                    or LIVE_SELLS_ENABLED
                )
                and not KILL_SWITCH
            ),

        "live_buy_active":
            bool(
                LIVE_TRADING
                and LIVE_EXECUTION_IMPLEMENTED
                and LIVE_BUYS_ENABLED
                and not KILL_SWITCH
            ),

        "live_sell_active":
            bool(
                LIVE_TRADING
                and LIVE_EXECUTION_IMPLEMENTED
                and LIVE_SELLS_ENABLED
                and not KILL_SWITCH
            ),

        "execution_provider":
            EXECUTION_PROVIDER,

        "stream_connected":
            bool(STREAM_CONNECTED),

        "stream_last_message_ts":
            (
                LAST_STREAM_MESSAGE_TS
                if LAST_STREAM_MESSAGE_TS > 0
                else None
            ),

        "stream_message_age_seconds":
            stream_message_age_seconds,

        "stream_last_event_ts":
            (
                LAST_STREAM_EVENT_TS
                if LAST_STREAM_EVENT_TS > 0
                else None
            ),

        "stream_last_error":
            (STREAM_LAST_ERROR or None),

        "stream_provider_message":
            (LAST_PUMPPORTAL_MESSAGE or None),

        "stream_alert_active":
            bool(STREAM_ALERT_ACTIVE),

        "pumpportal_wallet_balance_sol":
            PUMPPORTAL_WALLET_BALANCE_SOL,

        "pumpportal_balance_checked_ts":
            (
                PUMPPORTAL_BALANCE_CHECKED_TS
                if PUMPPORTAL_BALANCE_CHECKED_TS > 0
                else None
            ),

        "pumpportal_low_balance_threshold_sol":
            PUMPPORTAL_LOW_BALANCE_SOL,

        "pumpportal_low_balance":
            bool(PUMPPORTAL_BALANCE_ALERT_ACTIVE),

        "pumpportal_balance_last_error":
            (PUMPPORTAL_BALANCE_LAST_ERROR or None),

        "rpc_fallback_shadow_enabled":
            bool(RPC_FALLBACK_SHADOW_ENABLED),

        "rpc_fallback_last_poll_ts":
            (RPC_FALLBACK_LAST_POLL_TS or None),

        "rpc_fallback_last_success_ts":
            (RPC_FALLBACK_LAST_SUCCESS_TS or None),

        "rpc_fallback_last_error":
            (RPC_FALLBACK_LAST_ERROR or None),

        "shadow_mode_enabled":
            bool(SHADOW_MODE_ENABLED),

        "shadow_model_loaded":
            SHADOW_MODEL is not None,

        "shadow_model_version":
            (
                SHADOW_MODEL.model_version
                if SHADOW_MODEL is not None
                else None
            ),

        "shadow_model_last_error":
            (SHADOW_MODEL_LAST_ERROR or None),

        "shadow_challenger_model_loaded":
            SHADOW_CHALLENGER_MODEL is not None,

        "shadow_challenger_model_version":
            (
                SHADOW_CHALLENGER_MODEL.model_version
                if SHADOW_CHALLENGER_MODEL is not None
                else None
            ),

        "shadow_challenger_model_last_error":
            (SHADOW_CHALLENGER_LAST_ERROR or None),

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

            reasons,

            data_version,

            id,

            market_cap,

            sol_amount,

            (
                SELECT o.id
                FROM signal_outcomes o
                WHERE o.signal_id = evaluations.id
                LIMIT 1
            ),

            (
                SELECT o.price_at_signal
                FROM signal_outcomes o
                WHERE o.signal_id = evaluations.id
                LIMIT 1
            ),

            (
                SELECT o.status
                FROM signal_outcomes o
                WHERE o.signal_id = evaluations.id
                LIMIT 1
            )

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
                ),

        "data_version":
            int(r[13] or 0),

        "evaluation_id": int(r[14] or 0),

        "market_cap": r[15],

        "sol_amount": r[16],

        "outcome_id": (
            int(r[17]) if r[17] is not None else None
        ),

        "price_at_signal": r[18],

        "outcome_status": r[19]
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

            reasons,

            market_cap,

            sol_amount,

            data_version,

            (
                SELECT o.status
                FROM signal_outcomes o
                WHERE o.signal_id = evaluations.id
                LIMIT 1
            )

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
                ),

            "market_cap":
                r[7],

            "sol_amount":
                r[8],

            "data_version":
                int(r[9] or 0),

            "outcome_status":
                r[10]

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


        last_event_ts = conn.execute(
            """
            SELECT MAX(ts)
            FROM trades
            WHERE trader = ?
            """,
            (name,),
        ).fetchone()[0]

        event_age_seconds = (
            max(0.0, time.time() - float(last_event_ts))
            if last_event_ts is not None
            else None
        )

        quality = get_trader_quality_assessment(
            name
        )

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

                "last_event_ts":
                    last_event_ts,

                "event_age_seconds":
                    (
                        round(event_age_seconds, 3)
                        if event_age_seconds is not None
                        else None
                    ),

                "active_last_24h":
                    (
                        event_age_seconds is not None
                        and event_age_seconds <= 86400
                    ),

                "evaluations":
                    evaluations_count,

                "avg_score":
                    round(
                        avg_score or 0,
                        1
                    ),

                "quality":
                    quality["base_quality"],

                "effective_quality":
                    quality["effective_quality"],

                "quality_candidate":
                    quality["candidate_quality"],

                "quality_raw":
                    quality["raw_quality"],

                "quality_method":
                    "bayesian_target_rate",

                "quality_posterior_success_pct":
                    round(
                        quality["posterior_success_rate"] * 100,
                        2
                    ),

                "quality_dynamic_enabled":
                    quality["dynamic_enabled"],

                "quality_ready_for_review":
                    quality["ready_for_review"],

                "quality_blockers":
                    quality["blockers"],

                "quality_samples":
                    quality["samples"],

                "quality_tp25_pct":
                    quality["tp25_pct"],

                "quality_tp50_pct":
                    quality["tp50_pct"],

                "quality_sl10_pct":
                    quality["sl10_pct"],

                "quality_target_1":
                    quality["target_1"],

                "quality_target_0":
                    quality["target_0"],

                "quality_target_rate_pct":
                    quality["target_rate_pct"],

                # Mientras no haya evidencia suficiente el valor neutral es
                # solo un prior interno: hacia afuera se muestra sin nota.
                "quality_rated":
                    bool(quality["ready_for_review"]),

                "quality_display":
                    (
                        quality["effective_quality"]
                        if quality["ready_for_review"]
                        else TRADER_PROFILE_UNRATED_LABEL
                    ),

                "quality_label":
                    (
                        None
                        if quality["ready_for_review"]
                        else TRADER_PROFILE_UNRATED_LABEL
                    ),

            }
        )


    conn.close()

    return result


def _decode_helius_sync_addresses(raw_value):
    try:
        value = json.loads(raw_value or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("INVALID_HELIUS_SYNC_STATE_JSON") from exc
    if not isinstance(value, list):
        raise ValueError("INVALID_HELIUS_SYNC_STATE_ADDRESSES")
    return normalize_addresses(value)


def get_helius_webhook_sync_state(connection=None):
    owns_connection = connection is None
    conn = connection or db()
    try:
        row = conn.execute(
            """
            SELECT
                initialized,
                base_addresses_json,
                managed_tokens_json,
                pending_tokens_json,
                last_remote_addresses_json,
                last_desired_addresses_json,
                last_check_ts,
                last_success_ts,
                last_update_ts,
                last_error,
                updates
            FROM helius_webhook_sync_state
            WHERE id = 1
            """
        ).fetchone()
    finally:
        if owns_connection:
            conn.close()

    if row is None:
        raise RuntimeError("HELIUS_WEBHOOK_SYNC_STATE_MISSING")
    return {
        "initialized": bool(row[0]),
        "base_addresses": _decode_helius_sync_addresses(row[1]),
        "managed_tokens": _decode_helius_sync_addresses(row[2]),
        "pending_tokens": _decode_helius_sync_addresses(row[3]),
        "last_remote_addresses": _decode_helius_sync_addresses(row[4]),
        "last_desired_addresses": _decode_helius_sync_addresses(row[5]),
        "last_check_ts": row[6],
        "last_success_ts": row[7],
        "last_update_ts": row[8],
        "last_error": row[9],
        "updates": int(row[10] or 0),
    }


def _write_helius_webhook_sync_state(connection, **values):
    allowed = {
        "initialized",
        "base_addresses_json",
        "managed_tokens_json",
        "pending_tokens_json",
        "last_remote_addresses_json",
        "last_desired_addresses_json",
        "last_check_ts",
        "last_success_ts",
        "last_update_ts",
        "last_error",
        "updates",
    }
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"INVALID_HELIUS_SYNC_STATE_FIELDS:{sorted(unknown)}")
    if not values:
        return

    assignments = ", ".join(f"{key} = ?" for key in values)
    connection.execute(
        f"UPDATE helius_webhook_sync_state SET {assignments} WHERE id = 1",
        tuple(values.values()),
    )


def _encoded_helius_sync_addresses(addresses):
    return json.dumps(
        normalize_addresses(addresses),
        separators=(",", ":"),
    )


def _tracked_tokens_snapshot():
    for _ in range(3):
        try:
            return normalize_addresses(tuple(TRACKED_TOKENS))
        except RuntimeError:
            time.sleep(0)
    raise RuntimeError("TRACKED_TOKENS_CHANGED_DURING_SNAPSHOT")


def get_helius_webhook_sync_status():
    try:
        state = get_helius_webhook_sync_state()
        state_error = None
    except Exception as exc:
        state = {
            "initialized": False,
            "base_addresses": [],
            "managed_tokens": [],
            "pending_tokens": [],
            "last_remote_addresses": [],
            "last_desired_addresses": [],
            "last_check_ts": None,
            "last_success_ts": None,
            "last_update_ts": None,
            "last_error": None,
            "updates": 0,
        }
        state_error = f"{exc.__class__.__name__}:{exc}"

    tracked = _tracked_tokens_snapshot()
    cached_plan = plan_webhook_address_sync(
        state["last_remote_addresses"],
        tracked,
        managed_tokens=state["managed_tokens"],
        pending_tokens=state["pending_tokens"],
    )
    effective_error = state_error or state["last_error"]
    retry_seconds = (
        _helius_webhook_sync_retry_seconds(effective_error)
        if effective_error
        else None
    )
    next_retry_ts = (
        float(state["last_check_ts"]) + retry_seconds
        if state["last_check_ts"] is not None and retry_seconds is not None
        else None
    )
    return {
        "enabled": bool(HELIUS_WEBHOOK_SYNC_ENABLED),
        "apply": bool(HELIUS_WEBHOOK_SYNC_APPLY),
        "configured": bool(
            HELIUS_WEBHOOK_ENABLED
            and HELIUS_WEBHOOK_SECRET
            and HELIUS_API_KEY
            and HELIUS_WEBHOOK_ID
        ),
        "affects_decisions": False,
        "poll_seconds": HELIUS_WEBHOOK_SYNC_POLL_SECONDS,
        "audit_seconds": HELIUS_WEBHOOK_SYNC_AUDIT_SECONDS,
        "error_retry_seconds": HELIUS_WEBHOOK_SYNC_ERROR_RETRY_SECONDS,
        "rate_limit_retry_seconds": (
            HELIUS_WEBHOOK_SYNC_RATE_LIMIT_RETRY_SECONDS
        ),
        "initialized": state["initialized"],
        "tracked_tokens": len(tracked),
        "base_addresses": len(state["base_addresses"]),
        "managed_tokens": len(state["managed_tokens"]),
        "pending_tokens": len(state["pending_tokens"]),
        "last_remote_addresses": len(state["last_remote_addresses"]),
        "last_desired_addresses": len(state["last_desired_addresses"]),
        "planned_additions": len(cached_plan["additions"]),
        "planned_removals": len(cached_plan["removals"]),
        "last_check_ts": state["last_check_ts"],
        "last_success_ts": state["last_success_ts"],
        "last_update_ts": state["last_update_ts"],
        "last_error": effective_error,
        "retry_seconds": retry_seconds,
        "next_retry_ts": next_retry_ts,
        "updates": state["updates"],
    }


def _helius_webhook_sync_retry_seconds(last_error):
    error = str(last_error or "")
    if "HTTP_429" not in error:
        return HELIUS_WEBHOOK_SYNC_ERROR_RETRY_SECONDS

    retry_seconds = float(HELIUS_WEBHOOK_SYNC_RATE_LIMIT_RETRY_SECONDS)
    marker = "retry_after_seconds="
    if marker not in error:
        return retry_seconds
    raw_value = error.split(marker, 1)[1].split("|", 1)[0]
    try:
        server_retry_seconds = float(raw_value)
    except ValueError:
        return retry_seconds
    return max(retry_seconds, server_retry_seconds)


def sync_helius_webhook_tokens_once(
    now=None,
    fetch_webhook_fn=None,
    update_webhook_fn=None,
):
    """Reconcile tracked tokens without taking ownership of base addresses."""
    now = float(now if now is not None else time.time())
    fetch_webhook_fn = fetch_webhook_fn or fetch_helius_webhook
    update_webhook_fn = (
        update_webhook_fn or update_helius_webhook_addresses
    )

    if not HELIUS_WEBHOOK_SYNC_ENABLED:
        return {"status": "disabled", **get_helius_webhook_sync_status()}
    if not HELIUS_WEBHOOK_ENABLED:
        return {"status": "webhook_disabled", **get_helius_webhook_sync_status()}
    if not HELIUS_WEBHOOK_SECRET or not HELIUS_API_KEY or not HELIUS_WEBHOOK_ID:
        return {"status": "not_configured", **get_helius_webhook_sync_status()}
    if not HELIUS_WEBHOOK_SYNC_LOCK.acquire(blocking=False):
        return {"status": "busy", **get_helius_webhook_sync_status()}

    try:
        tracked = [
            token
            for token in _tracked_tokens_snapshot()
            if not token.startswith("DEMO")
        ]
        conn = db()
        try:
            state = get_helius_webhook_sync_state(conn)
            cached_desired = normalize_addresses(
                [*state["base_addresses"], *tracked]
            )
            check_age = (
                now - float(state["last_check_ts"])
                if state["last_check_ts"] is not None
                else None
            )
            cached_is_current = (
                state["initialized"]
                and cached_desired == state["last_desired_addresses"]
                and not state["last_error"]
                and check_age is not None
                and check_age < HELIUS_WEBHOOK_SYNC_AUDIT_SECONDS
            )
            if (
                state["last_error"]
                and check_age is not None
                and check_age < _helius_webhook_sync_retry_seconds(
                    state["last_error"]
                )
            ):
                return {
                    "status": "error_backoff",
                    **get_helius_webhook_sync_status(),
                }
            if cached_is_current and (
                not HELIUS_WEBHOOK_SYNC_APPLY
                or state["last_remote_addresses"] == cached_desired
            ):
                return {
                    "status": "cached",
                    **get_helius_webhook_sync_status(),
                }
        finally:
            conn.close()

        remote_webhook = fetch_webhook_fn(
            HELIUS_API_KEY,
            HELIUS_WEBHOOK_ID,
            timeout=HELIUS_WEBHOOK_SYNC_TIMEOUT_SECONDS,
        )
        plan = plan_webhook_address_sync(
            webhook_account_addresses(remote_webhook),
            tracked,
            managed_tokens=state["managed_tokens"],
            pending_tokens=state["pending_tokens"],
        )

        conn = db()
        try:
            _write_helius_webhook_sync_state(
                conn,
                initialized=1,
                base_addresses_json=_encoded_helius_sync_addresses(
                    plan["base_addresses"]
                ),
                last_remote_addresses_json=_encoded_helius_sync_addresses(
                    plan["remote_addresses"]
                ),
                last_desired_addresses_json=_encoded_helius_sync_addresses(
                    plan["desired_addresses"]
                ),
                last_check_ts=now,
                last_error=None,
            )
            conn.commit()
        finally:
            conn.close()

        changed = bool(plan["additions"] or plan["removals"])
        if not HELIUS_WEBHOOK_SYNC_APPLY:
            return {
                "status": "dry_run_changed" if changed else "dry_run_current",
                **get_helius_webhook_sync_status(),
            }

        previously_owned = set(state["managed_tokens"])
        previously_owned.update(state["pending_tokens"])
        next_managed = (
            previously_owned.intersection(tracked)
            | set(plan["additions"]).intersection(tracked)
        )

        if not changed:
            conn = db()
            try:
                _write_helius_webhook_sync_state(
                    conn,
                    managed_tokens_json=_encoded_helius_sync_addresses(
                        next_managed
                    ),
                    pending_tokens_json="[]",
                    last_success_ts=now,
                )
                conn.commit()
            finally:
                conn.close()
            return {"status": "current", **get_helius_webhook_sync_status()}

        # Persistir propiedad antes del PUT cierra la ventana de reinicio: si
        # Helius cambia y el proceso cae, esos tokens siguen siendo removibles.
        conn = db()
        try:
            _write_helius_webhook_sync_state(
                conn,
                pending_tokens_json=_encoded_helius_sync_addresses(next_managed),
            )
            conn.commit()
        finally:
            conn.close()

        update_helius_webhook_fn_result = update_webhook_fn(
            HELIUS_API_KEY,
            HELIUS_WEBHOOK_ID,
            remote_webhook,
            plan["desired_addresses"],
            timeout=HELIUS_WEBHOOK_SYNC_TIMEOUT_SECONDS,
        )
        confirmed = webhook_account_addresses(update_helius_webhook_fn_result)
        if confirmed != plan["desired_addresses"]:
            raise RuntimeError("HELIUS_WEBHOOK_UPDATE_NOT_CONFIRMED")

        conn = db()
        try:
            _write_helius_webhook_sync_state(
                conn,
                managed_tokens_json=_encoded_helius_sync_addresses(next_managed),
                pending_tokens_json="[]",
                last_remote_addresses_json=_encoded_helius_sync_addresses(
                    plan["desired_addresses"]
                ),
                last_success_ts=now,
                last_update_ts=now,
                last_error=None,
                updates=state["updates"] + 1,
            )
            conn.commit()
        finally:
            conn.close()
        return {"status": "updated", **get_helius_webhook_sync_status()}
    except Exception as exc:
        error_text = f"{exc.__class__.__name__}:{exc}"[:500]
        conn = db()
        try:
            _write_helius_webhook_sync_state(
                conn,
                last_check_ts=now,
                last_error=error_text,
            )
            conn.commit()
        finally:
            conn.close()
        return {"status": "error", **get_helius_webhook_sync_status()}
    finally:
        HELIUS_WEBHOOK_SYNC_LOCK.release()


async def update_helius_webhook_sync_alert(result):
    """Notify once while dynamic webhook synchronization is unhealthy."""
    global HELIUS_WEBHOOK_SYNC_ALERT_ACTIVE

    status = str(result.get("status") or "")
    error = str(result.get("last_error") or "")
    unhealthy = status in {"error", "error_backoff"} and bool(error)
    healthy = status in {
        "cached",
        "current",
        "updated",
        "dry_run_changed",
        "dry_run_current",
    }

    if unhealthy and not HELIUS_WEBHOOK_SYNC_ALERT_ACTIVE:
        HELIUS_WEBHOOK_SYNC_ALERT_ACTIVE = True
        if DISCORD_ALERT_WEBHOOK_URL:
            await send_discord_alert(
                "Pump Copilot: Helius token synchronization failed.\n"
                f"Reason: {error}\n"
                "Existing subscriptions remain active, but newly tracked "
                "tokens may be incomplete."
            )
    elif healthy and HELIUS_WEBHOOK_SYNC_ALERT_ACTIVE:
        HELIUS_WEBHOOK_SYNC_ALERT_ACTIVE = False
        if DISCORD_ALERT_WEBHOOK_URL:
            await send_discord_alert(
                "Pump Copilot: Helius token synchronization recovered."
            )


async def helius_webhook_sync_worker():
    while True:
        result = await asyncio.to_thread(sync_helius_webhook_tokens_once)
        if result.get("status") == "error":
            print("[HELIUS SYNC]", result.get("last_error"))
        await update_helius_webhook_sync_alert(result)
        await asyncio.sleep(HELIUS_WEBHOOK_SYNC_POLL_SECONDS)


def update_helius_standard_wss_state(**updates):
    with HELIUS_STANDARD_WSS_STATE_LOCK:
        HELIUS_STANDARD_WSS_STATE.update(updates)


def helius_standard_wss_retry_seconds(error, consecutive_failures):
    error_text = str(error).lower()
    if "429" in error_text or "rate limit" in error_text:
        return float(HELIUS_STANDARD_WSS_RATE_LIMIT_RETRY_SECONDS)
    exponent = max(0, min(int(consecutive_failures) - 1, 5))
    return float(min(60, HELIUS_STANDARD_WSS_RECONNECT_SECONDS * (2 ** exponent)))


def record_helius_standard_wss_notification(
    event,
    pump_logs,
    message_bytes,
    received_ts=None,
):
    received_ts = float(
        received_ts if received_ts is not None else time.time()
    )
    signature = event["signature"]
    conn = db()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO helius_standard_wss_notifications(
                signature, wallet, received_ts, slot, failed, pump_logs,
                message_bytes, subject_type
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                signature,
                event["wallet"],
                received_ts,
                event.get("slot"),
                int(bool(event.get("failed"))),
                int(bool(pump_logs)),
                max(0, int(message_bytes)),
                event.get("subject_type", "wallet"),
            ),
        )
        should_fetch = False
        if pump_logs and not event.get("failed"):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO helius_standard_wss_transactions(
                    signature, first_received_ts, status
                ) VALUES(?,?,'pending_fetch')
                """,
                (signature, received_ts),
            )
            should_fetch = bool(cursor.rowcount)
        conn.commit()
        return should_fetch
    finally:
        conn.close()


def finish_helius_standard_wss_transaction(
    signature,
    status,
    attempts,
    parsed_events=0,
    block_time=None,
    error=None,
    now=None,
):
    conn = db()
    try:
        conn.execute(
            """
            UPDATE helius_standard_wss_transactions
            SET fetched_ts = ?, block_time = ?, status = ?,
                fetch_attempts = ?, parsed_events = ?, last_error = ?
            WHERE signature = ?
            """,
            (
                float(now if now is not None else time.time()),
                block_time,
                status,
                int(attempts),
                int(parsed_events),
                str(error)[:500] if error else None,
                signature,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def pending_helius_standard_wss_transactions(now=None, limit=50):
    cutoff = float(now if now is not None else time.time()) - 900
    conn = db()
    try:
        return conn.execute(
            """
            SELECT signature, first_received_ts
            FROM helius_standard_wss_transactions
            WHERE status IN ('pending_fetch', 'observed')
              AND fetched_ts IS NULL
              AND first_received_ts >= ?
            ORDER BY first_received_ts, signature
            LIMIT ?
            """,
            (cutoff, max(1, min(int(limit), 50))),
        ).fetchall()
    finally:
        conn.close()


async def fetch_helius_standard_wss_transaction(
    event,
    received_ts,
    pending_signatures,
    semaphore,
    rate_lock,
    rate_state,
):
    signature = event["signature"]
    attempts = 0
    try:
        async with semaphore:
            receipt = None
            last_error = None
            for attempt in range(HELIUS_STANDARD_WSS_FETCH_RETRIES):
                attempts = attempt + 1
                async with rate_lock:
                    delay = rate_state["next_ts"] - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    rate_state["next_ts"] = (
                        time.monotonic()
                        + HELIUS_STANDARD_WSS_FETCH_INTERVAL_SECONDS
                    )
                try:
                    receipt = await asyncio.to_thread(
                        fetch_confirmed_transaction,
                        SOLANA_RPC_URL,
                        signature,
                    )
                except Exception as exc:
                    last_error = f"{exc.__class__.__name__}:{exc}"
                if receipt is not None:
                    break
                if attempt + 1 < HELIUS_STANDARD_WSS_FETCH_RETRIES:
                    await asyncio.sleep(0.5 * (2 ** attempt))

            if receipt is None:
                error = last_error or "TRANSACTION_NOT_AVAILABLE"
                await asyncio.to_thread(
                    finish_helius_standard_wss_transaction,
                    signature,
                    "fetch_failed",
                    attempts,
                    error=error,
                )
                update_helius_standard_wss_state(last_error=error)
                return

            result = await asyncio.to_thread(
                record_helius_webhook_transactions,
                [receipt],
                received_ts=received_ts,
                persist_inbox=HELIUS_STANDARD_WSS_APPLY,
                persist_observation=False,
            )
            parsed_events = int(result.get("parsed_events") or 0)
            status = (
                "applied" if HELIUS_STANDARD_WSS_APPLY
                else "observed"
            )
            if not parsed_events:
                status = "unparsed"
            await asyncio.to_thread(
                finish_helius_standard_wss_transaction,
                signature,
                status,
                attempts,
                parsed_events,
                receipt.get("blockTime"),
            )
            update_helius_standard_wss_state(
                last_success_ts=time.time(),
                last_error=None,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = f"{exc.__class__.__name__}:{exc}"[:500]
        try:
            await asyncio.to_thread(
                finish_helius_standard_wss_transaction,
                signature,
                "processing_failed",
                attempts,
                error=error,
            )
        finally:
            update_helius_standard_wss_state(last_error=error)
    finally:
        pending_signatures.discard(signature)
        update_helius_standard_wss_state(
            pending_fetches=len(pending_signatures)
        )


async def schedule_helius_standard_wss_fetch(
    event,
    received_ts,
    pending_signatures,
    fetch_tasks,
    semaphore,
    rate_lock,
    rate_state,
):
    signature = event["signature"]
    if signature in pending_signatures:
        return
    if len(pending_signatures) >= HELIUS_STANDARD_WSS_MAX_PENDING:
        await asyncio.to_thread(
            finish_helius_standard_wss_transaction,
            signature,
            "queue_full",
            0,
            error="HELIUS_STANDARD_WSS_QUEUE_FULL",
        )
        return
    pending_signatures.add(signature)
    update_helius_standard_wss_state(
        pending_fetches=len(pending_signatures)
    )
    task = asyncio.create_task(
        fetch_helius_standard_wss_transaction(
            event,
            received_ts,
            pending_signatures,
            semaphore,
            rate_lock,
            rate_state,
        )
    )
    fetch_tasks.add(task)
    task.add_done_callback(fetch_tasks.discard)


async def sync_helius_standard_wss_tokens(
    websocket,
    wallets,
    subscriptions,
    subscription_kinds,
    pending_requests,
    pending_kinds,
    pending_request_started,
    pending_unsubscribes,
    pending_unsubscribe_started,
    next_request_id,
):
    desired, omitted = select_tracked_tokens(
        _tracked_tokens_snapshot(),
        wallets,
        HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS,
    )
    desired = set(desired)
    active = {
        address for subscription_id, address in subscriptions.items()
        if subscription_kinds.get(subscription_id) == "token"
    }
    pending_additions = {
        address for request_id, address in pending_requests.items()
        if pending_kinds.get(request_id) == "token"
    }
    pending_removals = set(pending_unsubscribes.values())

    for subscription_id, address in sorted(subscriptions.items()):
        if (subscription_kinds.get(subscription_id) != "token"
                or address in desired
                or subscription_id in pending_removals):
            continue
        pending_unsubscribes[next_request_id] = subscription_id
        pending_unsubscribe_started[next_request_id] = time.monotonic()
        await websocket.send(json.dumps(
            build_logs_unsubscribe_request(next_request_id, subscription_id),
            separators=(",", ":"),
        ))
        next_request_id += 1

    for token in sorted(desired - active - pending_additions):
        pending_requests[next_request_id] = token
        pending_kinds[next_request_id] = "token"
        pending_request_started[next_request_id] = time.monotonic()
        await websocket.send(json.dumps(
            build_logs_subscribe_request(next_request_id, token),
            separators=(",", ":"),
        ))
        next_request_id += 1

    update_helius_standard_wss_state(
        tracked_tokens_desired=len(desired),
        tracked_tokens_omitted=omitted,
    )
    return next_request_id


async def helius_standard_wss_worker():
    try:
        url = build_helius_standard_wss_url(
            HELIUS_API_KEY,
            HELIUS_STANDARD_WSS_URL,
        )
    except ValueError as exc:
        update_helius_standard_wss_state(last_error=str(exc))
        return
    if not url:
        update_helius_standard_wss_state(
            last_error="HELIUS_STANDARD_WSS_NOT_CONFIGURED"
        )
        return

    pending_signatures = set()
    fetch_tasks = set()
    semaphore = asyncio.Semaphore(HELIUS_STANDARD_WSS_MAX_IN_FLIGHT)
    rate_lock = asyncio.Lock()
    rate_state = {"next_ts": 0.0}
    consecutive_failures = 0

    while True:
        wallets = sorted({
            str(wallet).strip() for wallet in WATCHED.values()
            if str(wallet).strip()
        })
        if not wallets:
            update_helius_standard_wss_state(
                connected=False,
                subscriptions=0,
                tracked_token_subscriptions=0,
                tracked_tokens_desired=0,
                tracked_tokens_omitted=0,
                last_error="HELIUS_STANDARD_WSS_NO_WALLETS",
            )
            await asyncio.sleep(HELIUS_STANDARD_WSS_RECONNECT_SECONDS)
            continue

        try:
            async with websockets.connect(
                url,
                ping_interval=30,
                ping_timeout=30,
                close_timeout=10,
                max_size=4_000_000,
            ) as websocket:
                now = time.time()
                update_helius_standard_wss_state(
                    connected=True,
                    subscriptions=0,
                    tracked_token_subscriptions=0,
                    tracked_tokens_desired=0,
                    tracked_tokens_omitted=0,
                    last_connect_ts=now,
                    last_error=None,
                    retry_seconds=None,
                    next_retry_ts=None,
                )
                consecutive_failures = 0
                pending_requests = {}
                pending_kinds = {}
                pending_request_started = {}
                pending_unsubscribes = {}
                pending_unsubscribe_started = {}
                subscriptions = {}
                subscription_kinds = {}
                subscribed_wallets = set()
                recovered_pending = False
                for request_id, wallet in enumerate(wallets, start=1):
                    pending_requests[request_id] = wallet
                    pending_kinds[request_id] = "wallet"
                    pending_request_started[request_id] = time.monotonic()
                    await websocket.send(json.dumps(
                        build_logs_subscribe_request(request_id, wallet),
                        separators=(",", ":"),
                    ))
                next_request_id = len(wallets) + 1
                last_token_poll = 0.0

                while True:
                    if any(
                        time.monotonic() - started > 60
                        for started in (
                            *pending_request_started.values(),
                            *pending_unsubscribe_started.values(),
                        )
                    ):
                        raise RuntimeError(
                            "HELIUS_STANDARD_WSS_SUBSCRIPTION_TIMEOUT"
                        )
                    if (HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED
                            and time.monotonic() - last_token_poll
                            >= HELIUS_STANDARD_WSS_TOKEN_POLL_SECONDS):
                        next_request_id = await sync_helius_standard_wss_tokens(
                            websocket,
                            wallets,
                            subscriptions,
                            subscription_kinds,
                            pending_requests,
                            pending_kinds,
                            pending_request_started,
                            pending_unsubscribes,
                            pending_unsubscribe_started,
                            next_request_id,
                        )
                        last_token_poll = time.monotonic()
                    try:
                        raw = await asyncio.wait_for(
                            websocket.recv(),
                            timeout=(
                                HELIUS_STANDARD_WSS_TOKEN_POLL_SECONDS
                                if HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED
                                else 60
                            ),
                        )
                    except asyncio.TimeoutError:
                        if len(subscribed_wallets) < len(wallets):
                            raise RuntimeError(
                                "HELIUS_STANDARD_WSS_SUBSCRIPTION_TIMEOUT"
                            )
                        continue
                    received_ts = time.time()
                    update_helius_standard_wss_state(
                        last_message_ts=received_ts
                    )
                    payload = decode_wss_message(raw)
                    try:
                        response_id = int(payload.get("id"))
                    except (TypeError, ValueError):
                        response_id = None
                    if (response_id in pending_requests
                            or response_id in pending_unsubscribes) and payload.get(
                                "error"
                            ):
                        raise RuntimeError(
                            "HELIUS_STANDARD_WSS_SUBSCRIPTION_REJECTED:"
                            + json.dumps(
                                payload["error"],
                                separators=(",", ":"),
                            )[:300]
                        )
                    if response_id in pending_unsubscribes:
                        if payload.get("result") is not True:
                            raise RuntimeError(
                                "HELIUS_STANDARD_WSS_UNSUBSCRIBE_FAILED"
                            )
                        removed_id = pending_unsubscribes.pop(response_id)
                        pending_unsubscribe_started.pop(response_id)
                        subscriptions.pop(removed_id, None)
                        subscription_kinds.pop(removed_id, None)
                        update_helius_standard_wss_state(
                            subscriptions=len(subscriptions),
                            tracked_token_subscriptions=sum(
                                kind == "token"
                                for kind in subscription_kinds.values()
                            ),
                        )
                        continue
                    confirmation = subscription_confirmation(
                        payload, pending_requests
                    )
                    if confirmation:
                        subscription_id, address = confirmation
                        kind = pending_kinds.pop(response_id)
                        pending_requests.pop(response_id)
                        pending_request_started.pop(response_id)
                        subscriptions[subscription_id] = address
                        subscription_kinds[subscription_id] = kind
                        if kind == "wallet":
                            subscribed_wallets.add(address)
                        update_helius_standard_wss_state(
                            subscriptions=len(subscriptions),
                            tracked_token_subscriptions=sum(
                                value == "token"
                                for value in subscription_kinds.values()
                            ),
                        )
                        if (not recovered_pending
                                and len(subscribed_wallets) == len(wallets)):
                            recovered_pending = True
                            interrupted = await asyncio.to_thread(
                                pending_helius_standard_wss_transactions
                            )
                            for signature, first_received_ts in interrupted:
                                await schedule_helius_standard_wss_fetch(
                                    {"signature": signature},
                                    first_received_ts,
                                    pending_signatures,
                                    fetch_tasks,
                                    semaphore,
                                    rate_lock,
                                    rate_state,
                                )
                        continue

                    event = parse_logs_notification(payload, subscriptions)
                    if event is None:
                        continue
                    subscription_id = payload["params"]["subscription"]
                    event["subject_type"] = subscription_kinds[
                        int(subscription_id)
                    ]
                    pump_logs = invokes_program(
                        event["logs"],
                        (PUMP_PROGRAM_ID, PUMP_AMM_PROGRAM_ID),
                    )
                    if pump_logs:
                        update_helius_standard_wss_state(
                            last_pump_log_ts=received_ts
                        )
                    should_fetch = await asyncio.to_thread(
                        record_helius_standard_wss_notification,
                        event,
                        pump_logs,
                        len(
                            raw if isinstance(raw, bytes)
                            else raw.encode("utf-8")
                        ),
                        received_ts,
                    )
                    if not should_fetch:
                        continue
                    await schedule_helius_standard_wss_fetch(
                        event,
                        received_ts,
                        pending_signatures,
                        fetch_tasks,
                        semaphore,
                        rate_lock,
                        rate_state,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            retry_seconds = helius_standard_wss_retry_seconds(
                exc, consecutive_failures
            )
            with HELIUS_STANDARD_WSS_STATE_LOCK:
                reconnects = int(
                    HELIUS_STANDARD_WSS_STATE["reconnects"] or 0
                ) + 1
            update_helius_standard_wss_state(
                connected=False,
                subscriptions=0,
                tracked_token_subscriptions=0,
                tracked_tokens_desired=0,
                reconnects=reconnects,
                last_error=f"{exc.__class__.__name__}:{exc}"[:500],
                retry_seconds=retry_seconds,
                next_retry_ts=time.time() + retry_seconds,
            )
            await asyncio.sleep(retry_seconds)


def record_helius_webhook_transactions(
    payload,
    received_ts=None,
    persist_inbox=True,
    persist_observation=True,
):
    """Registra lo que llegó por webhook. Observacional: no dispara nada.

    Devuelve cuántas transacciones se vieron, cuántas resultaron ser
    operaciones Pump relevantes por wallet o token, y cuántas transacciones
    ya estaban registradas.
    """
    received_ts = float(
        received_ts if received_ts is not None else time.time()
    )

    if not isinstance(payload, list):
        payload = [payload]

    wallets_by_address = {
        wallet: trader
        for trader, wallet in WATCHED.items()
    }
    tracked_mints = set(TRACKED_TOKENS)

    seen = 0
    parsed_events = 0
    duplicates = 0

    conn = db()

    try:
        for receipt in payload[:HELIUS_WEBHOOK_MAX_TRANSACTIONS]:
            if not isinstance(receipt, dict):
                continue

            seen += 1

            transaction = receipt.get("transaction") or {}
            signatures = transaction.get("signatures") or []
            signature = signatures[0] if signatures else None

            if not signature:
                continue

            block_time = receipt.get("blockTime")
            message = transaction.get("message") or {}
            if not isinstance(message, dict):
                message = {}
            account_keys = message.get("accountKeys") or []
            if not isinstance(account_keys, list):
                account_keys = []
            account_addresses = set()
            for key in account_keys:
                address = key.get("pubkey") if isinstance(key, dict) else key
                if isinstance(address, str):
                    account_addresses.add(address)
            loaded_addresses = (receipt.get("meta") or {}).get(
                "loadedAddresses"
            ) or {}
            if not isinstance(loaded_addresses, dict):
                loaded_addresses = {}
            for addresses in (
                loaded_addresses.get("writable") or [],
                loaded_addresses.get("readonly") or [],
            ):
                account_addresses.update(
                    address for address in addresses
                    if isinstance(address, str)
                )
            observed_wallets = account_addresses.intersection(
                wallets_by_address
            )
            pump_program_in_accounts = int(bool(
                account_addresses.intersection((
                    PUMP_PROGRAM_ID, PUMP_AMM_PROGRAM_ID
                ))
            ))

            # Una transacción puede tocar varias wallets vigiladas. El resumen
            # por transacción conserva la primera, pero el inbox guarda cada
            # operación atribuida a todas ellas.
            matched_trader = None
            matched_wallet = None
            matched_events_by_index = {}

            for wallet, trader in wallets_by_address.items():
                try:
                    events = parse_watched_wallet_pump_events(
                        receipt, wallet, signature
                    )
                except Exception as exc:
                    print("[HELIUS WEBHOOK] Parse failed:", repr(exc))
                    events = []

                if events:
                    if matched_wallet is None:
                        matched_trader = trader
                        matched_wallet = wallet
                    for parsed_event in events:
                        normalized_event = parsed_event["event"]
                        event_index = market_event_index(
                            normalized_event, required=True
                        )
                        matched_events_by_index[event_index] = (
                            parsed_event,
                            wallet,
                            trader,
                        )

            try:
                token_events = parse_tracked_token_pump_events(
                    receipt,
                    tracked_mints,
                    signature,
                )
            except Exception as exc:
                print("[HELIUS TOKEN WEBHOOK] Parse failed:", repr(exc))
                token_events = []

            for parsed_event in token_events:
                normalized_event = parsed_event["event"]
                event_index = market_event_index(
                    normalized_event, required=True
                )
                event_wallet = str(
                    normalized_event.get("traderPublicKey") or ""
                ).strip()
                # Solo las wallets configuradas tienen identidad de trader.
                # Un prefijo de dirección sería una etiqueta inventada que
                # podría confundirse con una identidad medida al consumir el
                # inbox más adelante.
                event_trader = wallets_by_address.get(event_wallet)
                matched_events_by_index.setdefault(
                    event_index,
                    (parsed_event, event_wallet, event_trader),
                )

            matched_events = [
                matched_events_by_index[index]
                for index in sorted(matched_events_by_index)
            ]

            if matched_events and matched_wallet is None:
                _, matched_wallet, matched_trader = matched_events[0]

            parsed = matched_events[0][0]["event"] if matched_events else None
            parsed_events += len(matched_events)

            if persist_inbox:
                for parsed_event, event_wallet, event_trader in matched_events:
                    normalized_event = parsed_event["event"]
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO market_event_inbox(
                            signature, event_index, event_index_scheme,
                            source, wallet, trader,
                            mint, side, pool, block_time, block_event_ts,
                            received_ts, event_json
                        )
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            signature,
                            # Del evento, que es lo que se serializa más abajo
                            # en `event_json`: columna y JSON no discrepan.
                            market_event_index(
                                normalized_event, required=True
                            ),
                            "ordinal-v2",
                            "helius",
                            event_wallet,
                            event_trader,
                            normalized_event.get("mint"),
                            normalized_event.get("txType"),
                            normalized_event.get("pool"),
                            float(block_time) if block_time else None,
                            market_event_block_ts(normalized_event),
                            received_ts,
                            json.dumps(
                                normalized_event,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )

            raw_sample = None

            if (
                persist_observation
                and parsed is None
                and HELIUS_WEBHOOK_RAW_SAMPLES
            ):
                stored_samples = conn.execute(
                    "SELECT COUNT(*) FROM helius_webhook_events "
                    "WHERE raw_sample IS NOT NULL"
                ).fetchone()[0]

                if stored_samples < HELIUS_WEBHOOK_RAW_SAMPLES:
                    try:
                        raw_sample = json.dumps(receipt)[
                            :HELIUS_WEBHOOK_RAW_SAMPLE_CHARS
                        ]
                    except Exception:
                        raw_sample = None

            observation_inserted = False
            if persist_observation:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO helius_webhook_events(
                        signature, wallet, trader, block_time,
                        received_ts, parsed, side, mint, pool, raw_sample
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        signature,
                        matched_wallet,
                        matched_trader,
                        float(block_time) if block_time else None,
                        received_ts,
                        1 if parsed else 0,
                        (parsed or {}).get("txType"),
                        (parsed or {}).get("mint"),
                        (parsed or {}).get("pool"),
                        raw_sample,
                    ),
                )
                observation_inserted = bool(cursor.rowcount)
                if not observation_inserted:
                    duplicates += 1

            if observation_inserted:
                parsed_wallets = {
                    wallet for _, wallet, _ in matched_events
                    if wallet in observed_wallets
                }
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO helius_webhook_wallet_observations(
                        signature, wallet, received_ts, parsed,
                        pump_program_in_accounts
                    ) VALUES(?,?,?,?,?)
                    """,
                    [
                        (signature, wallet, received_ts,
                         int(wallet in parsed_wallets),
                         pump_program_in_accounts)
                        for wallet in observed_wallets
                    ],
                )

        conn.commit()
    finally:
        conn.close()

    return {
        "seen": seen,
        "parsed_events": parsed_events,
        "duplicates": duplicates,
    }


@app.post("/api/helius-webhook")
async def helius_webhook(
    request: FastAPIRequest,
    authorization: str = Header(default=""),
):
    """Recibe y preserva transacciones; el consumidor decide si se aplican."""

    if not HELIUS_WEBHOOK_ENABLED:
        raise HTTPException(status_code=404, detail="NOT_FOUND")

    # Sin secreto configurado no se acepta nada: es una ruta pública que
    # escribe en la base.
    if not HELIUS_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="HELIUS_WEBHOOK_SECRET_NOT_CONFIGURED",
        )

    if not secrets.compare_digest(
        str(authorization or ""),
        HELIUS_WEBHOOK_SECRET,
    ):
        raise HTTPException(status_code=401, detail="INVALID_WEBHOOK_SECRET")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="INVALID_JSON")

    result = await asyncio.to_thread(
        record_helius_webhook_transactions,
        payload,
    )

    return {"ok": True, **result}


@app.get("/api/helius-webhook-stats")
def api_helius_webhook_stats(
    x_app_token: str = Header(default="")
):
    """Comparación de entrega y latencia entre el webhook y PumpPortal."""

    auth(x_app_token)

    conn = db()

    totals = conn.execute(
        """
        SELECT
            COUNT(*),
            SUM(parsed),
            COUNT(block_time)
        FROM helius_webhook_events
        """
    ).fetchone()

    # Latencia del webhook: desde que la transacción entró en un bloque hasta
    # que llegó el push.
    webhook_latency = conn.execute(
        """
        SELECT
            COUNT(*),
            AVG(received_ts - block_time),
            MIN(received_ts - block_time),
            MAX(received_ts - block_time)
        FROM helius_webhook_events
        WHERE parsed = 1
        AND block_time IS NOT NULL
        AND received_ts >= block_time
        """
    ).fetchone()

    # Latencia de PumpPortal, medida sobre las operaciones que sí entregó:
    # el momento en que las guardamos contra el tiempo de bloque que conocemos
    # por el monitor RPC.
    #
    # `trades` no dice qué transporte escribió cada fila, y con el consumidor
    # del inbox activo Helius también escribe ahí con `ts` igual al tiempo de
    # bloque: esas filas darían latencia cero y hundirían el promedio. Quién
    # ganó cada operación queda en `processed_market_events.source`. Una firma
    # con operaciones ganadas por ambos transportes se descarta entera, porque
    # `trades` no tiene índice para separar sus filas.
    stream_latency = conn.execute(
        """
        SELECT
            COUNT(*),
            AVG(trades.ts - rpc.block_time),
            MIN(trades.ts - rpc.block_time),
            MAX(trades.ts - rpc.block_time)
        FROM rpc_fallback_events AS rpc
        JOIN trades ON trades.signature = rpc.signature
        WHERE rpc.block_time IS NOT NULL
        AND trades.ts >= rpc.block_time
        AND EXISTS(
            SELECT 1 FROM processed_market_events AS won
            WHERE won.signature = trades.signature
            AND won.source = 'live'
        )
        AND NOT EXISTS(
            SELECT 1 FROM processed_market_events AS won
            WHERE won.signature = trades.signature
            AND won.source = 'helius'
        )
        """
    ).fetchone()

    # Operaciones que el webhook vio y el stream no.
    #
    # La referencia es la reserva de identidad del stream, no `trades`: el
    # stream reserva toda operación que entrega —también las de tokens
    # seguidos por wallets no vigiladas, que nunca llegan a `trades`— y el
    # consumidor del inbox reserva con `source = 'helius'`, así que lo que él
    # escribe en `trades` no cuenta como entregado por PumpPortal.
    #
    # Límite: si el consumidor gana una operación antes que el stream, la
    # copia del stream se descarta como duplicada sin dejar rastro y acá cuenta
    # como vista solo por el webhook. Con el sondeo del consumidor en segundos
    # y PumpPortal en milisegundos, es el caso raro.
    only_webhook = conn.execute(
        """
        SELECT COUNT(*)
        FROM helius_webhook_events AS hook
        WHERE hook.parsed = 1
        AND NOT EXISTS(
            SELECT 1 FROM processed_market_events AS won
            WHERE won.signature = hook.signature
            AND won.source = 'live'
        )
        """
    ).fetchone()[0]

    recent_rows = conn.execute(
        """
        SELECT signature, block_time, received_ts, parsed, trader
        FROM helius_webhook_events
        ORDER BY received_ts DESC
        LIMIT 10
        """
    ).fetchall()

    sample_row = conn.execute(
        """
        SELECT COUNT(*) FROM helius_webhook_events
        WHERE raw_sample IS NOT NULL
        """
    ).fetchone()
    measurement_start = conn.execute(
        "SELECT MIN(received_ts) "
        "FROM helius_webhook_wallet_observations"
    ).fetchone()[0]
    wallet_windows = {}
    now = time.time()
    for label, seconds in (("24h", 86400), ("7d", 604800)):
        rows = conn.execute(
            """
            SELECT wallet, COUNT(*), SUM(parsed),
                   COUNT(pump_program_in_accounts),
                   SUM(CASE WHEN pump_program_in_accounts = 1
                       THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pump_program_in_accounts = 1
                       AND parsed = 0 THEN 1 ELSE 0 END)
            FROM helius_webhook_wallet_observations
            WHERE received_ts >= ?
            GROUP BY wallet
            ORDER BY COUNT(*) DESC, wallet
            """,
            (now - seconds,),
        ).fetchall()
        traders_by_wallet = {
            wallet: trader for trader, wallet in WATCHED.items()
        }
        wallet_windows[label] = [
            {
                "wallet": wallet,
                "trader": traders_by_wallet.get(wallet),
                "transactions": int(count),
                "pump_transactions": int(parsed or 0),
                "pump_percent": round(100 * (parsed or 0) / count, 2),
                "program_classified_transactions": int(classified),
                "pump_program_in_accounts": int(program_count),
                "unparsed_with_pump_program": int(unparsed_program),
            }
            for (
                wallet, count, parsed, classified, program_count,
                unparsed_program
            ) in rows
        ]

    inbox_total = conn.execute(
        "SELECT COUNT(*) FROM market_event_inbox WHERE source = 'helius'"
    ).fetchone()[0]
    multi_event_transactions = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT signature
            FROM market_event_inbox
            WHERE source = 'helius'
            GROUP BY signature
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]
    inbox_status_rows = conn.execute(
        """
        SELECT status, COUNT(*)
        FROM market_event_inbox
        WHERE source = 'helius'
        GROUP BY status
        """
    ).fetchall()

    conn.close()

    recent_events = [
        {
            "signature": row[0],
            "block_time": row[1],
            "received_ts": row[2],
            "parsed": bool(row[3]),
            "trader": row[4],
        }
        for row in recent_rows
    ]

    unparsed_sample = {
        "stored": int(sample_row[0] or 0),
        "endpoint": "/api/helius-webhook-sample",
    }

    def summarize(row):
        return {
            "samples": int(row[0] or 0),
            "avg_seconds": round(row[1], 3) if row[1] is not None else None,
            "min_seconds": round(row[2], 3) if row[2] is not None else None,
            "max_seconds": round(row[3], 3) if row[3] is not None else None,
        }

    return {
        "enabled": bool(HELIUS_WEBHOOK_ENABLED),
        "observational": not MARKET_EVENT_INBOX_CONSUMER_ENABLED,
        "affects_decisions": bool(MARKET_EVENT_INBOX_CONSUMER_ENABLED),
        "webhook_sync": get_helius_webhook_sync_status(),
        "transactions_received": int(totals[0] or 0),
        "pump_events_parsed": int(totals[1] or 0),
        "normalized_events_observed": int(inbox_total or 0),
        "multi_event_transactions": int(multi_event_transactions or 0),
        "inbox_status": {
            str(status): int(count)
            for status, count in inbox_status_rows
        },
        "inbox_validation": {
            "enabled": bool(MARKET_EVENT_INBOX_VALIDATION_ENABLED),
            "affects_decisions": False,
            "batch_size": MARKET_EVENT_INBOX_VALIDATION_BATCH_SIZE,
            "poll_seconds": MARKET_EVENT_INBOX_VALIDATION_POLL_SECONDS,
            "lease_seconds": MARKET_EVENT_INBOX_VALIDATION_LEASE_SECONDS,
        },
        "inbox_consumer": {
            "enabled": bool(MARKET_EVENT_INBOX_CONSUMER_ENABLED),
            "affects_decisions": bool(MARKET_EVENT_INBOX_CONSUMER_ENABLED),
            "affects_live_execution": bool(MARKET_EVENT_INBOX_CONSUMER_ENABLED),
            "affects_live_buys": False,
            "affects_live_exits": bool(MARKET_EVENT_INBOX_CONSUMER_ENABLED),
            "activation_ts": get_market_event_inbox_activation_ts(),
            "batch_size": MARKET_EVENT_INBOX_CONSUMER_BATCH_SIZE,
            "poll_seconds": MARKET_EVENT_INBOX_CONSUMER_POLL_SECONDS,
            "lease_seconds": MARKET_EVENT_INBOX_CONSUMER_LEASE_SECONDS,
        },
        "with_block_time": int(totals[2] or 0),
        "webhook_latency": summarize(webhook_latency),
        "pumpportal_latency": summarize(stream_latency),
        "parsed_only_in_webhook": int(only_webhook or 0),
        "recent_events": recent_events,
        "unparsed_sample": unparsed_sample,
        "wallet_delivery": {
            "measurement_started_ts": measurement_start,
            "transactions_can_match_multiple_wallets": True,
            "windows": wallet_windows,
        },
    }


@app.get("/api/helius-credit-usage")
def api_helius_credit_usage(x_app_token: str = Header(default="")):
    auth(x_app_token)
    if not HELIUS_API_KEY or not HELIUS_PROJECT_ID:
        return {
            "configured": False,
            "error": "HELIUS_ADMIN_NOT_CONFIGURED",
        }
    try:
        usage = fetch_helius_credit_usage(HELIUS_API_KEY, HELIUS_PROJECT_ID)
    except HeliusCreditUsageError as exc:
        return {"configured": True, "error": str(exc)}
    return {"configured": True, "error": None, **usage}


@app.get("/api/helius-standard-wss-stats")
def api_helius_standard_wss_stats(
    x_app_token: str = Header(default="")
):
    auth(x_app_token)
    now = time.time()
    cutoff = now - 86400
    conn = db()
    try:
        notification = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(pump_logs), 0),
                   COALESCE(SUM(failed), 0),
                   COALESCE(SUM(message_bytes), 0),
                   MIN(received_ts), MAX(received_ts)
            FROM helius_standard_wss_notifications
            WHERE received_ts >= ?
            """,
            (cutoff,),
        ).fetchone()
        transactions = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(fetch_attempts), 0),
                   COALESCE(SUM(parsed_events), 0),
                   COALESCE(SUM(CASE WHEN fetched_ts IS NULL THEN 1 ELSE 0 END), 0)
            FROM helius_standard_wss_transactions
            WHERE first_received_ts >= ?
            """,
            (cutoff,),
        ).fetchone()
        statuses = conn.execute(
            """
            SELECT status, COUNT(*)
            FROM helius_standard_wss_transactions
            WHERE first_received_ts >= ?
            GROUP BY status
            """,
            (cutoff,),
        ).fetchall()
        wallets = conn.execute(
            """
            SELECT wallet, COUNT(*), COALESCE(SUM(pump_logs), 0)
            FROM helius_standard_wss_notifications
            WHERE received_ts >= ? AND subject_type = 'wallet'
            GROUP BY wallet
            ORDER BY COUNT(*) DESC, wallet
            """,
            (cutoff,),
        ).fetchall()
        token_notifications = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT wallet)
            FROM helius_standard_wss_notifications
            WHERE received_ts >= ? AND subject_type = 'token'
            """,
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()

    first_ts = notification[4]
    measurement_seconds = (
        max(0.0, min(86400.0, now - float(first_ts)))
        if first_ts is not None else 0.0
    )
    observed_credits = (
        (float(notification[3] or 0) / 100000.0) * 2.0
        + float(transactions[1] or 0)
    )
    projected_monthly_credits = None
    if measurement_seconds >= 3600:
        projected_monthly_credits = round(
            observed_credits * (30 * 86400 / measurement_seconds),
            2,
        )
    with HELIUS_STANDARD_WSS_STATE_LOCK:
        runtime = dict(HELIUS_STANDARD_WSS_STATE)
    watched_count = len({
        str(wallet).strip() for wallet in WATCHED.values()
        if str(wallet).strip()
    })
    wallet_subscriptions_ready = bool(
        runtime["connected"]
        and runtime["subscriptions"]
        - runtime["tracked_token_subscriptions"] == watched_count
    )
    tracked_token_subscriptions_ready = bool(
        HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED
        and runtime["connected"]
        and runtime["tracked_token_subscriptions"]
        == runtime["tracked_tokens_desired"]
        and runtime["tracked_tokens_omitted"] == 0
    )
    traders_by_wallet = {
        wallet: trader for trader, wallet in WATCHED.items()
    }
    try:
        configured = bool(build_helius_standard_wss_url(
            HELIUS_API_KEY, HELIUS_STANDARD_WSS_URL
        ))
        configuration_error = None
    except ValueError as exc:
        configured = False
        configuration_error = str(exc)
    return {
        "enabled": bool(HELIUS_STANDARD_WSS_ENABLED),
        "apply": bool(HELIUS_STANDARD_WSS_APPLY),
        "configured": configured,
        "configuration_error": configuration_error,
        "track_tokens_enabled": bool(
            HELIUS_STANDARD_WSS_TRACK_TOKENS_ENABLED
        ),
        "max_tracked_tokens": HELIUS_STANDARD_WSS_MAX_TRACKED_TOKENS,
        "wallet_subscriptions_ready": wallet_subscriptions_ready,
        "tracked_token_subscriptions_ready": tracked_token_subscriptions_ready,
        "affects_decisions": bool(
            HELIUS_STANDARD_WSS_APPLY
            and MARKET_EVENT_INBOX_CONSUMER_ENABLED
        ),
        "runtime": runtime,
        "last_24h": {
            "notifications": int(notification[0] or 0),
            "pump_log_notifications": int(notification[1] or 0),
            "failed_notifications": int(notification[2] or 0),
            "message_bytes": int(notification[3] or 0),
            "first_received_ts": first_ts,
            "last_received_ts": notification[5],
            "transactions_selected": int(transactions[0] or 0),
            "rpc_fetch_attempts": int(transactions[1] or 0),
            "parsed_events": int(transactions[2] or 0),
            "pending_transaction_fetches": int(transactions[3] or 0),
            "token_notifications": int(token_notifications[0] or 0),
            "tokens_observed": int(token_notifications[1] or 0),
            "statuses": {
                str(status): int(count) for status, count in statuses
            },
            "wallets": [
                {
                    "wallet": wallet,
                    "trader": traders_by_wallet.get(wallet),
                    "notifications": int(count),
                    "pump_log_notifications": int(pump_count),
                }
                for wallet, count, pump_count in wallets
            ],
        },
        "credit_estimate": {
            "measurement_seconds": round(measurement_seconds, 3),
            "observed_credits_approx": round(observed_credits, 3),
            "projected_monthly_credits_approx": projected_monthly_credits,
            "projection_ready": measurement_seconds >= 3600,
            "free_plan_monthly_credits": 1000000,
            "assumptions": (
                "2 credits per 0.1 MB uncompressed WSS traffic plus "
                "1 credit per getTransaction attempt; excludes connection "
                "and unrelated project usage"
            ),
        },
    }


@app.get("/api/helius-webhook-sample")
def api_helius_webhook_sample(
    x_app_token: str = Header(default="")
):
    """Payload crudo de una transacción que no parseó, para diagnóstico."""

    auth(x_app_token)

    conn = db()
    row = conn.execute(
        """
        SELECT signature, received_ts, raw_sample
        FROM helius_webhook_events
        WHERE raw_sample IS NOT NULL
        ORDER BY received_ts DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    if not row:
        return {"available": False}

    try:
        payload = json.loads(row[2])
    except Exception:
        payload = None

    return {
        "available": True,
        "signature": row[0],
        "received_ts": row[1],
        "payload": payload,
        "raw": row[2] if payload is None else None,
    }


@app.get("/api/watched-wallets")
def api_watched_wallets(
    x_app_token: str = Header(default="")
):
    """Entrega de datos por wallet vigilada: detecta silencios individuales."""

    auth(x_app_token)

    activity = get_watched_wallet_activity()

    return {
        "stream_connected": bool(STREAM_CONNECTED),
        "silence_threshold_hours": round(
            WATCHED_WALLET_SILENCE_SECONDS / 3600,
            2
        ),
        "resubscribe_minutes": round(
            WATCHED_RESUBSCRIBE_SECONDS / 60,
            1
        ),
        "watched": len(WATCHED),
        "silent": sum(1 for item in activity if item["silent"]),
        "never_seen": sum(1 for item in activity if item["never_seen"]),
        "wallets": activity,
        # Permite ver qué contestó PumpPortal al suscribir cuentas, que antes
        # se perdía en cuanto llegaba cualquier otro mensaje.
        "provider_messages": list(PUMPPORTAL_MESSAGE_LOG),
    }


@app.get("/api/rpc-fallback-stats")
def api_rpc_fallback_stats(
    x_app_token: str = Header(default="")
):
    """Auditoría RPC de trades Pump ausentes del stream de PumpPortal."""

    auth(x_app_token)
    return get_rpc_fallback_stats()


@app.get("/api/trader-quality-profile")
def api_trader_quality_profile(
    x_app_token: str = Header(default="")
):
    """Perfil integral de calidad. Observacional: no mueve dinero ni decide."""

    auth(x_app_token)

    return {
        "observational": True,
        "affects_decisions": False,
        "unrated_label": TRADER_PROFILE_UNRATED_LABEL,
        "minimum_entry_samples": TRADER_QUALITY_MIN_SAMPLES,
        "minimum_exit_cycles": TRADER_PROFILE_MIN_CYCLES,
        "recency_half_life_days": TRADER_PROFILE_RECENCY_HALFLIFE_DAYS,
        "weights": TRADER_PROFILE_WEIGHTS,
        "traders": get_trader_quality_profiles(),
    }

@app.get("/api/training-stats")
def api_training_stats():
    return get_training_dataset_stats()


@app.get("/api/training-stats-by-trader")
def api_training_stats_by_trader():
    return get_training_stats_by_trader()


@app.get("/api/training-checkpoint-freshness")
def api_training_checkpoint_freshness():
    return get_training_checkpoint_freshness()

@app.get("/api/training-expired-preview")
def api_training_expired_preview(
    limit: int = 20
):
    return get_training_expired_preview(
        limit
    )


@app.get("/api/training-dataset")
def api_training_dataset(
    x_app_token: str = Header(default=""),
    limit: int = 5000,
):
    auth(x_app_token)

    rows = get_training_dataset_rows()
    safe_limit = max(1, min(int(limit or 5000), 5000))

    return {
        "data_version": DATA_VERSION,
        "count": len(rows),
        "returned": min(len(rows), safe_limit),
        "rows": rows[:safe_limit],
    }


@app.get("/api/training-dataset-preview")
def api_training_dataset_preview(
    limit: int = 20
):
    rows = get_training_dataset_rows()

    safe_limit = max(
        1,
        min(
            int(limit or 20),
            100
        )
    )

    return {
        "data_version": DATA_VERSION,
        "count": len(rows),
        "rows": rows[:safe_limit],
    }


@app.get("/api/shadow-predictions")
def api_shadow_predictions(
    x_app_token: str = Header(default=""),
    limit: int = 100,
    before_id: int = 0,
):
    auth(x_app_token)
    safe_limit = max(1, min(int(limit or 100), 1000))
    rows = get_shadow_predictions(
        limit=safe_limit,
        before_id=before_id,
    )
    return {
        "count": len(rows),
        "rows": rows,
        "next_before_id": (
            rows[-1]["prediction_id"]
            if len(rows) == safe_limit
            else None
        ),
    }


@app.get("/api/shadow-stats")
def api_shadow_stats(
    x_app_token: str = Header(default=""),
):
    auth(x_app_token)
    return get_shadow_stats()


@app.get("/api/live-execution-readiness")
def api_live_execution_readiness(
    x_app_token: str = Header(default=""),
    execution_side: str = "",
):
    auth(x_app_token)
    normalized_side = str(execution_side or "").strip().lower()
    if normalized_side not in ("", "buy", "sell"):
        raise HTTPException(
            status_code=400,
            detail="INVALID_EXECUTION_SIDE",
        )
    return get_live_execution_readiness(normalized_side)


@app.get("/api/live-positions")
def api_live_positions(
    x_app_token: str = Header(default=""),
    limit: int = 100,
):
    auth(x_app_token)
    return get_live_position_summary(limit)


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
