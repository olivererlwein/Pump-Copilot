"""Read current Pump and canonical PumpSwap prices without trading effects."""

import base64
import binascii
import struct

from solders.pubkey import Pubkey

from solana_rpc_fallback import (
    PUMP_AMM_PROGRAM_ID,
    PUMP_PROGRAM_ID,
    WSOL_MINT,
    _rpc_request,
)


PUMP = Pubkey.from_string(PUMP_PROGRAM_ID)
AMM = Pubkey.from_string(PUMP_AMM_PROGRAM_ID)
WSOL = Pubkey.from_string(WSOL_MINT)
TOKEN_PROGRAMS = {
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
BONDING_CURVE_DISCRIMINATOR = bytes((23, 183, 248, 55, 96, 216, 172, 96))
POOL_DISCRIMINATOR = bytes((241, 154, 109, 4, 17, 177, 109, 188))


def account_addresses(mint):
    mint_key = Pubkey.from_string(mint)
    curve = Pubkey.find_program_address(
        [b"bonding-curve", bytes(mint_key)], PUMP
    )[0]
    authority = Pubkey.find_program_address(
        [b"pool-authority", bytes(mint_key)], PUMP
    )[0]
    pool = Pubkey.find_program_address(
        [b"pool", b"\x00\x00", bytes(authority), bytes(mint_key), bytes(WSOL)],
        AMM,
    )[0]
    return str(curve), str(pool)


def _data(account, owner):
    if not account or account.get("owner") != owner:
        return None
    encoded = account.get("data")
    if not isinstance(encoded, list) or not encoded or not isinstance(encoded[0], str):
        return None
    try:
        return base64.b64decode(encoded[0], validate=True)
    except (ValueError, binascii.Error):
        return None


def _mint_info(account):
    if not account or account.get("owner") not in TOKEN_PROGRAMS:
        return None
    raw = _data(account, account["owner"])
    if raw is None or len(raw) < 45:
        return None
    supply = struct.unpack_from("<Q", raw, 36)[0]
    decimals = raw[44]
    if supply <= 0 or decimals > 18:
        return None
    return supply, decimals


def _curve_price(account, mint_info):
    raw = _data(account, PUMP_PROGRAM_ID)
    if raw is None or len(raw) < 49 or raw[:8] != BONDING_CURVE_DISCRIMINATOR:
        return "invalid_curve", None
    if len(raw) >= 115 and raw[83:115] not in (bytes(32), bytes(WSOL)):
        return "unsupported_quote", None
    if raw[48]:
        return "complete", None
    tokens, quote = struct.unpack_from("<QQ", raw, 8)
    if tokens <= 0 or quote <= 0:
        return "invalid_curve", None
    supply, decimals = mint_info
    price = (quote / 1_000_000_000) / (tokens / 10**decimals)
    return "curve", (price, price * supply / 10**decimals)


def _pool_vaults(account, mint):
    raw = _data(account, PUMP_AMM_PROGRAM_ID)
    if raw is None:
        return "pool_missing", None
    if (len(raw) < 245 or raw[:8] != POOL_DISCRIMINATOR
            or raw[43:75] != bytes(Pubkey.from_string(mint))
            or raw[75:107] != bytes(WSOL)):
        return "unsupported_pool", None
    base = str(Pubkey.from_bytes(raw[139:171]))
    quote = str(Pubkey.from_bytes(raw[171:203]))
    virtual_quote = int.from_bytes(raw[245:261], "little", signed=True) if len(raw) >= 261 else 0
    return "amm", (base, quote, virtual_quote)


def _vault_amount(account, mint, pool):
    if not account or account.get("owner") not in TOKEN_PROGRAMS:
        return None
    raw = _data(account, account["owner"])
    if (raw is None or len(raw) < 72 or raw[:32] != bytes(Pubkey.from_string(mint))
            or raw[32:64] != bytes(Pubkey.from_string(pool))):
        return None
    return struct.unpack_from("<Q", raw, 64)[0]


def fetch_account_prices(rpc_url, mints, rpc_request=_rpc_request):
    """Return current prices only; never infer a missing pool or historical price."""
    mints = list(dict.fromkeys(mints))
    if not mints:
        return {}
    if len(mints) > 20:
        raise ValueError("ACCOUNT_PRICE_TOO_MANY_MINTS")

    addresses = []
    for mint in mints:
        curve, pool = account_addresses(mint)
        addresses.extend((mint, curve, pool))
    first = rpc_request(rpc_url, "getMultipleAccounts", [
        addresses, {"encoding": "base64", "commitment": "confirmed"},
    ])
    slot = first["context"]["slot"]
    accounts = first["value"]
    if len(accounts) != len(addresses):
        raise ValueError("ACCOUNT_PRICE_INCOMPLETE_BATCH")

    prices = {}
    pending = []
    vault_addresses = []
    for index, mint in enumerate(mints):
        mint_account, curve_account, pool_account = accounts[index * 3:index * 3 + 3]
        mint_info = _mint_info(mint_account)
        if mint_info is None:
            prices[mint] = {"status": "invalid_mint", "slot": slot}
            continue
        status, result = _curve_price(curve_account, mint_info)
        if status == "curve":
            prices[mint] = {
                "status": status, "price_sol": result[0],
                "market_cap_sol": result[1], "slot": slot,
            }
            continue
        if status not in ("complete", "invalid_curve"):
            prices[mint] = {"status": status, "slot": slot}
            continue
        pool_status, vaults = _pool_vaults(pool_account, mint)
        if vaults is None:
            prices[mint] = {"status": pool_status, "slot": slot}
            continue
        pending.append((mint, addresses[index * 3 + 2], mint_info, vaults))
        vault_addresses.extend(vaults[:2])

    if pending:
        second = rpc_request(rpc_url, "getMultipleAccounts", [
            vault_addresses, {
                "encoding": "base64", "commitment": "confirmed",
                "minContextSlot": slot,
            },
        ])
        vault_accounts = second["value"]
        if len(vault_accounts) != len(vault_addresses):
            raise ValueError("ACCOUNT_PRICE_INCOMPLETE_VAULT_BATCH")
        vault_slot = second["context"]["slot"]
        for index, (mint, pool, mint_info, (_, _, virtual_quote)) in enumerate(pending):
            base = _vault_amount(vault_accounts[index * 2], mint, pool)
            quote = _vault_amount(vault_accounts[index * 2 + 1], WSOL_MINT, pool)
            effective_quote = (quote + virtual_quote) if quote is not None else 0
            if base is None or base <= 0 or effective_quote <= 0:
                prices[mint] = {"status": "invalid_vaults", "slot": vault_slot}
                continue
            supply, decimals = mint_info
            price = (effective_quote / 1_000_000_000) / (base / 10**decimals)
            prices[mint] = {
                "status": "amm", "price_sol": price,
                "market_cap_sol": price * supply / 10**decimals,
                "slot": vault_slot,
            }
    return prices
