"""Manually verify one Pump mint's creation with at most two RPC attempts."""

import argparse
import json
import os

from dotenv import load_dotenv
from solders.pubkey import Pubkey

import solana_rpc_fallback as rpc


# Pump's published IDL: create and create_v2 both use the mint as account 0.
CREATE_DISCRIMINATORS = {
    bytes((24, 30, 200, 40, 5, 28, 7, 119)): "create",
    bytes((214, 144, 76, 236, 95, 139, 49, 180)): "create_v2",
}


def decode_base58(value):
    number = 0
    for character in value:
        number = number * 58 + rpc.BASE58_ALPHABET.index(character)
    size = (number.bit_length() + 7) // 8
    return b"\0" * (len(value) - len(value.lstrip("1"))) + number.to_bytes(size, "big")


def verify_receipt(mint, row, receipt):
    if not isinstance(receipt, dict):
        return {"status": "receipt_unavailable"}
    transaction = receipt.get("transaction") or {}
    if (transaction.get("signatures") or [None])[0] != row["signature"]:
        return {"status": "signature_mismatch"}
    meta = receipt.get("meta")
    if not isinstance(meta, dict) or "err" not in meta:
        return {"status": "missing_transaction_meta"}
    if meta["err"] is not None:
        return {"status": "failed_transaction"}
    slot = receipt.get("slot")
    row_slot = row.get("slot")
    if (isinstance(slot, bool) or not isinstance(slot, int) or slot <= 0
            or isinstance(row_slot, bool) or not isinstance(row_slot, int)
            or slot != row_slot):
        return {"status": "slot_mismatch"}
    block_time = receipt.get("blockTime")
    if (isinstance(block_time, bool) or not isinstance(block_time, int)
            or block_time <= 0):
        return {"status": "missing_block_time"}
    row_block_time = row.get("blockTime")
    if row_block_time is not None and (
        isinstance(row_block_time, bool) or not isinstance(row_block_time, int)
        or row_block_time != block_time
    ):
        return {"status": "block_time_mismatch"}

    for instruction in (transaction.get("message") or {}).get("instructions") or []:
        if instruction.get("programId") != rpc.PUMP_PROGRAM_ID:
            continue
        accounts = instruction.get("accounts") or []
        if not accounts or accounts[0] != mint:
            continue
        data = instruction.get("data")
        if not isinstance(data, str):
            continue
        try:
            kind = CREATE_DISCRIMINATORS.get(decode_base58(data)[:8])
        except ValueError:
            continue
        if kind:
            return {
                "status": "verified_create", "instruction": kind,
                "signature": row["signature"], "block_time": block_time,
            }
    return {"status": "oldest_not_pump_create"}


def probe(mint, rpc_url, fetch_signatures=None, fetch_transaction=None):
    fetch_signatures = fetch_signatures or rpc.fetch_signatures_for_address
    fetch_transaction = fetch_transaction or rpc.fetch_confirmed_transaction
    rows = fetch_signatures(rpc_url, mint, limit=1000)
    if len(rows) >= 1000:
        return {"status": "history_truncated", "signatures_seen": len(rows)}
    if not rows:
        return {"status": "no_signatures"}
    successful = [row for row in rows if row.get("err") is None]
    if not successful:
        return {"status": "no_successful_signature"}
    oldest = successful[-1]
    result = verify_receipt(mint, oldest, fetch_transaction(rpc_url, oldest["signature"]))
    result["signatures_seen"] = len(rows)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mint", required=True)
    parser.add_argument("--execute", action="store_true", help="Spend up to two archival RPC attempts")
    args = parser.parse_args()
    try:
        mint = str(Pubkey.from_string(args.mint))
    except ValueError:
        raise SystemExit("INVALID_MINT") from None
    if not args.execute:
        print(json.dumps({"mint": mint, "status": "dry_run", "max_rpc_attempts": 2}))
        return
    load_dotenv()
    rpc_url = os.getenv("SOLANA_RPC_URL", "")
    if not rpc_url.startswith("https://"):
        raise SystemExit("SOLANA_RPC_URL_REQUIRED")
    # Dedicated one-shot process: disable the shared helper's automatic retries.
    rpc.RPC_RETRY_DELAYS_SECONDS = ()
    try:
        result = probe(mint, rpc_url)
    except Exception as exc:
        result = {"status": "rpc_error", "error_type": type(exc).__name__}
    print(json.dumps({"mint": mint, **result}, sort_keys=True))


if __name__ == "__main__":
    main()
