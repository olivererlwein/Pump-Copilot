"""Strict, observational parsing for Pump trades on Solana RPC."""

import base64
import json
import struct
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL_MINT = "So11111111111111111111111111111111111111112"

PUMP_TRADE_EVENT = bytes((189, 219, 127, 211, 78, 230, 97, 238))
PUMP_AMM_BUY_EVENT = bytes((103, 244, 82, 31, 44, 245, 119, 119))
PUMP_AMM_SELL_EVENT = bytes((62, 47, 55, 10, 165, 3, 220, 42))

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
LAMPORTS_PER_SOL = 1_000_000_000
PUMP_TOKEN_SUPPLY = 1_000_000_000

RPC_MIN_REQUEST_INTERVAL_SECONDS = 0.2
RPC_RETRY_DELAYS_SECONDS = (0.5, 1.0, 2.0)
_RPC_REQUEST_LOCK = threading.Lock()
_RPC_LAST_REQUEST_TS = 0.0


def _wait_for_rpc_slot():
    global _RPC_LAST_REQUEST_TS

    with _RPC_REQUEST_LOCK:
        wait_seconds = (
            RPC_MIN_REQUEST_INTERVAL_SECONDS
            - (time.monotonic() - _RPC_LAST_REQUEST_TS)
        )
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _RPC_LAST_REQUEST_TS = time.monotonic()


def _rpc_request(rpc_url, method, params, timeout=15):
    request = Request(
        rpc_url,
        data=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    payload = None
    for attempt in range(len(RPC_RETRY_DELAYS_SECONDS) + 1):
        _wait_for_rpc_slot()
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            break
        except HTTPError as ex:
            retryable = ex.code == 429 or 500 <= ex.code < 600
            if not retryable or attempt >= len(RPC_RETRY_DELAYS_SECONDS):
                raise
            retry_after = ex.headers.get("Retry-After") if ex.headers else None
            try:
                if retry_after is None:
                    raise ValueError
                delay = max(
                    float(retry_after),
                    RPC_RETRY_DELAYS_SECONDS[attempt],
                )
            except (TypeError, ValueError):
                delay = RPC_RETRY_DELAYS_SECONDS[attempt]
            time.sleep(delay)
        except (TimeoutError, URLError):
            if attempt >= len(RPC_RETRY_DELAYS_SECONDS):
                raise
            time.sleep(RPC_RETRY_DELAYS_SECONDS[attempt])
    if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
        raise ValueError(f"INVALID_SOLANA_RPC_RESPONSE:{method}")
    return payload["result"]


def fetch_signatures_for_address(rpc_url, wallet, limit=25, until=None):
    options = {
        "commitment": "confirmed",
        "limit": max(1, min(int(limit), 1000)),
    }
    if until:
        options["until"] = str(until)
    result = _rpc_request(
        rpc_url,
        "getSignaturesForAddress",
        [wallet, options],
    )
    if not isinstance(result, list):
        raise ValueError("INVALID_SOLANA_SIGNATURE_LIST")
    return [row for row in result if isinstance(row, dict) and row.get("signature")]


def fetch_confirmed_transaction(rpc_url, signature):
    result = _rpc_request(
        rpc_url,
        "getTransaction",
        [signature, {
            "encoding": "jsonParsed",
            "commitment": "confirmed",
            "maxSupportedTransactionVersion": 0,
        }],
    )
    if result is not None and not isinstance(result, dict):
        raise ValueError("INVALID_SOLANA_TRANSACTION")
    return result


def _base58_encode(raw):
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(raw) - len(raw.lstrip(b"\0"))
    return ("1" * leading_zeroes) + (encoded or ("1" if not leading_zeroes else ""))


def _event_payloads(receipt):
    logs = (receipt.get("meta") or {}).get("logMessages") or []
    for index, line in enumerate(logs):
        if not isinstance(line, str) or not line.startswith("Program data: "):
            continue
        try:
            yield index, base64.b64decode(line.split(": ", 1)[1], validate=True)
        except (ValueError, TypeError):
            continue


def _token_balances(receipt):
    meta = receipt.get("meta") or {}
    result = {"pre": {}, "post": {}}
    for phase, key in (("pre", "preTokenBalances"), ("post", "postTokenBalances")):
        for row in meta.get(key) or []:
            if not isinstance(row, dict):
                continue
            owner = row.get("owner")
            mint = row.get("mint")
            amount_info = row.get("uiTokenAmount") or {}
            raw_amount = amount_info.get("amount")
            decimals = amount_info.get("decimals")
            if (
                not owner
                or not mint
                or not isinstance(raw_amount, str)
                or decimals is None
            ):
                continue
            try:
                amount = int(raw_amount)
                decimals = int(decimals)
            except (TypeError, ValueError):
                continue
            if amount < 0 or decimals < 0:
                continue
            record = result[phase].setdefault(
                (str(owner), str(mint)),
                {"raw": 0, "decimals": decimals},
            )
            if record["decimals"] != decimals:
                raise ValueError("INCONSISTENT_TOKEN_DECIMALS")
            record["raw"] += amount
    return result


def _token_decimals(balances, mint):
    values = {
        record["decimals"]
        for phase in balances.values()
        for (owner, row_mint), record in phase.items()
        if owner and row_mint == mint
    }
    if len(values) != 1:
        raise ValueError("TOKEN_DECIMALS_UNAVAILABLE")
    return values.pop()


def _post_token_amount(balances, owner, mint, decimals):
    record = balances["post"].get((owner, mint))
    if record:
        return record["raw"] / (10 ** decimals)
    if (owner, mint) in balances["pre"]:
        return 0.0
    raise ValueError("USER_TOKEN_BALANCE_UNAVAILABLE")


def _parse_pump_trade(payload, balances, signature):
    minimum_size = 8 + 32 + 8 + 8 + 1 + 32 + 8 + 8 + 8
    if len(payload) < minimum_size or payload[:8] != PUMP_TRADE_EVENT:
        return None

    offset = 8
    mint = _base58_encode(payload[offset:offset + 32])
    offset += 32
    sol_lamports, token_raw = struct.unpack_from("<QQ", payload, offset)
    offset += 16
    is_buy = bool(payload[offset])
    offset += 1
    user = _base58_encode(payload[offset:offset + 32])
    offset += 32
    timestamp, virtual_sol, virtual_tokens = struct.unpack_from("<qQQ", payload, offset)

    if virtual_tokens <= 0:
        return None

    decimals = _token_decimals(balances, mint)
    scale = 10 ** decimals
    market_cap = (virtual_sol / virtual_tokens) * scale

    return {
        "program": PUMP_PROGRAM_ID,
        "event_name": "TradeEvent",
        "event": {
            "signature": signature,
            # Momento on-chain del evento. Va adentro por la misma razón que
            # `eventIndex`: el evento es lo único que se serializa y se vuelve
            # a leer, así que un dato que viajara al lado se perdería ahí.
            "blockEventTs": timestamp,
            "txType": "buy" if is_buy else "sell",
            "mint": mint,
            "traderPublicKey": user,
            "solAmount": sol_lamports / LAMPORTS_PER_SOL,
            "tokenAmount": token_raw / scale,
            "newTokenBalance": _post_token_amount(
                balances, user, mint, decimals
            ),
            "marketCapSol": market_cap,
            "vSolInBondingCurve": virtual_sol / LAMPORTS_PER_SOL,
            "vTokensInBondingCurve": virtual_tokens / scale,
            "pool": "pump",
        },
    }


def _parse_pump_amm_trade(payload, balances, signature):
    if payload[:8] == PUMP_AMM_BUY_EVENT:
        side = "buy"
        event_name = "BuyEvent"
    elif payload[:8] == PUMP_AMM_SELL_EVENT:
        side = "sell"
        event_name = "SellEvent"
    else:
        return None

    fixed_prefix_size = 8 + (14 * 8) + (2 * 32)
    if len(payload) < fixed_prefix_size:
        return None

    values = struct.unpack_from("<q13Q", payload, 8)
    timestamp = values[0]
    amounts = values[1:]
    token_raw = amounts[0]
    # For both current BuyEvent and SellEvent, this is the quote amount after
    # the LP fee. It matches PumpPortal's solAmount semantics.
    sol_lamports = amounts[11]
    pool_offset = 8 + (14 * 8)
    pool = _base58_encode(payload[pool_offset:pool_offset + 32])
    user = _base58_encode(payload[pool_offset + 32:pool_offset + 64])
    base_mints = {
        mint
        for phase in balances.values()
        for (owner, mint), record in phase.items()
        if owner == pool and mint != WSOL_MINT and record["raw"] >= 0
    }
    if len(base_mints) != 1:
        raise ValueError("PUMP_AMM_BASE_MINT_AMBIGUOUS")
    mint = base_mints.pop()
    decimals = _token_decimals(balances, mint)
    scale = 10 ** decimals

    pool_base = balances["post"].get((pool, mint))
    pool_quote = balances["post"].get((pool, WSOL_MINT))
    if not pool_base or not pool_quote or pool_base["raw"] <= 0:
        raise ValueError("PUMP_AMM_POOL_BALANCES_UNAVAILABLE")
    market_cap = (
        (pool_quote["raw"] / LAMPORTS_PER_SOL)
        / (pool_base["raw"] / scale)
        * PUMP_TOKEN_SUPPLY
    )

    return {
        "program": PUMP_AMM_PROGRAM_ID,
        "event_name": event_name,
        "event": {
            "signature": signature,
            # Ver la nota en `_parse_pump_trade`.
            "blockEventTs": timestamp,
            "txType": side,
            "mint": mint,
            "traderPublicKey": user,
            "solAmount": sol_lamports / LAMPORTS_PER_SOL,
            "tokenAmount": token_raw / scale,
            "newTokenBalance": _post_token_amount(
                balances, user, mint, decimals
            ),
            "marketCapSol": market_cap,
            "pool": "pump-amm",
        },
    }


def _is_signed_by(message, wallet):
    """¿Firmó ``wallet`` esta transacción?

    Solana devuelve ``accountKeys`` en dos formas según la codificación:

    - ``jsonParsed`` (lo que pide ``getTransaction`` acá): diccionarios con
      ``pubkey`` y ``signer``.
    - nativa (lo que entregan los webhooks): lista de strings en base58, donde
      los firmantes son los primeros ``header.numRequiredSignatures``.

    Aceptar solo la primera haría que una fuente en formato nativo se
    descartara entera y en silencio, que es indistinguible de "no hay datos".
    """
    account_keys = message.get("accountKeys") or []

    if any(isinstance(row, dict) for row in account_keys):
        return any(
            isinstance(row, dict)
            and row.get("pubkey") == wallet
            and bool(row.get("signer"))
            for row in account_keys
        )

    required_value = (message.get("header") or {}).get(
        "numRequiredSignatures"
    )
    if required_value is None:
        return False

    try:
        required_signatures = int(required_value)
    except (TypeError, ValueError):
        return False

    if required_signatures <= 0:
        return False

    return wallet in [
        key
        for key in account_keys[:required_signatures]
        if isinstance(key, str)
    ]


def _parse_official_pump_events(receipt, signature):
    """Parse official Pump/PumpSwap events without choosing a consumer."""
    if not isinstance(receipt, dict) or not signature:
        return []
    meta = receipt.get("meta")
    if not isinstance(meta, dict) or meta.get("err") is not None:
        return []

    transaction = receipt.get("transaction") or {}
    transaction_signatures = transaction.get("signatures") or []
    if not transaction_signatures or transaction_signatures[0] != signature:
        return []

    balances = _token_balances(receipt)
    logs = meta.get("logMessages") or []
    invoked_programs = {
        program_id
        for program_id in (PUMP_PROGRAM_ID, PUMP_AMM_PROGRAM_ID)
        if any(
            isinstance(line, str)
            and line.startswith(f"Program {program_id} invoke ")
            for line in logs
        )
    }
    parsed = []
    for _, payload in _event_payloads(receipt):
        try:
            result = None
            if PUMP_PROGRAM_ID in invoked_programs:
                result = _parse_pump_trade(
                    payload, balances, signature
                )
            if result is None and PUMP_AMM_PROGRAM_ID in invoked_programs:
                result = _parse_pump_amm_trade(
                    payload, balances, signature
                )
        except (ValueError, struct.error):
            continue
        if result is not None:
            # El índice va adentro del evento normalizado, no al lado.
            #
            # Lo que se guarda y se vuelve a leer es el evento serializado: si
            # el índice viajara como hermano, se perdería en ese salto y dos
            # operaciones de la misma transacción volverían a compartir
            # identidad, que es justo lo que el índice existe para evitar. La
            # firma ya viaja adentro; son dos mitades de lo mismo y no tienen
            # que poder separarse.
            #
            # Y cuenta operaciones Pump, no líneas de log. La posición dentro de
            # `logMessages` es un detalle de cómo encontramos el evento acá, y
            # depende del proveedor: para una transacción de una sola operación
            # daría 1 por este camino y 0 por PumpPortal, que no manda índice.
            # La misma operación tendría dos identidades según quién la trajo, y
            # se guardaría dos veces. El ordinal entre operaciones parseadas es
            # independiente del proveedor, que es lo que la identidad necesita.
            result["event"]["eventIndex"] = len(parsed)
            parsed.append(result)
    return parsed


def parse_watched_wallet_pump_events(receipt, wallet, signature):
    """Return official Pump events signed by and attributed to ``wallet``."""
    if not isinstance(receipt, dict) or not wallet or not signature:
        return []

    transaction = receipt.get("transaction") or {}
    message = transaction.get("message") or {}
    if not _is_signed_by(message, wallet):
        return []

    return [
        parsed
        for parsed in _parse_official_pump_events(receipt, signature)
        if parsed["event"].get("traderPublicKey") == wallet
    ]


def parse_tracked_token_pump_events(receipt, tracked_mints, signature):
    """Return official Pump events for the requested token mints.

    Unlike the watched-wallet parser, this path does not require the trader to
    be one of our configured wallets. Token follow-up events are emitted by the
    Pump programs and may be signed by any market participant.
    """
    mints = {
        str(mint).strip()
        for mint in (tracked_mints or ())
        if str(mint or "").strip()
    }
    if not mints:
        return []

    return [
        parsed
        for parsed in _parse_official_pump_events(receipt, signature)
        if parsed["event"].get("mint") in mints
    ]
