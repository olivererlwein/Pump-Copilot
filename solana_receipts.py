"""Conservative accounting of finalized buys from Solana jsonParsed receipts."""

from decimal import Decimal, localcontext

WSOL_MINT = "So11111111111111111111111111111111111111112"


def unsigned_integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("INVALID_RECEIPT_INTEGER")
    return value


def parse_buy_receipt(receipt, signature, wallet, mint):
    """Return exact balance deltas, not an inferred swap price or fee breakdown.

    Net SOL debit includes account deposits/refunds and provider fees/tips.
    The RPC network fee is reported separately, not subtracted a second time.
    """
    try:
        return _parse_buy_receipt(receipt, signature, wallet, mint)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("INCOMPLETE_SOLANA_RECEIPT") from exc


def parse_sell_receipt(receipt, signature, wallet, mint):
    """Return exact tokens sold and the all-in net SOL proceeds."""
    try:
        return _parse_sell_receipt(receipt, signature, wallet, mint)
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("INCOMPLETE_SOLANA_RECEIPT") from exc


def _parse_buy_receipt(receipt, signature, wallet, mint):
    balances = _parse_receipt_balances(receipt, signature, wallet, mint)
    acquired = balances["target_delta"]
    if acquired <= 0 or len(balances["target_decimals"]) != 1:
        raise ValueError("NO_UNAMBIGUOUS_TOKEN_ACQUISITION")
    decimals = balances["target_decimals"].pop()
    native_debit = -balances["native_delta"]
    net_debit = native_debit - balances["wsol_delta"]
    fee = balances["fee"]
    if net_debit <= fee:
        raise ValueError("NO_UNAMBIGUOUS_SOL_DEBIT")
    with localcontext() as context:
        context.prec = 100
        tokens = Decimal(acquired).scaleb(-decimals)
        cash_cost_per_token = Decimal(net_debit).scaleb(-9) / tokens
    return {
        "signature": signature, "wallet": wallet, "mint": mint,
        "slot": balances["slot"], "block_time": balances["block_time"],
        "token_amount_raw": str(acquired), "token_decimals": decimals,
        "token_amount": format(tokens, "f"),
        "native_debit_lamports": str(native_debit),
        "wsol_debit_lamports": str(-balances["wsol_delta"]),
        "net_sol_debit_lamports": str(net_debit),
        "network_fee_lamports": str(fee),
        "cash_cost_per_token_sol": format(cash_cost_per_token, "f"),
        "cost_basis": "net_cash_including_fees_and_account_deposits",
    }


def _parse_sell_receipt(receipt, signature, wallet, mint):
    balances = _parse_receipt_balances(receipt, signature, wallet, mint)
    sold = -balances["target_delta"]
    if sold <= 0 or len(balances["target_decimals"]) != 1:
        raise ValueError("NO_UNAMBIGUOUS_TOKEN_SALE")
    decimals = balances["target_decimals"].pop()
    native_credit = balances["native_delta"]
    net_credit = native_credit + balances["wsol_delta"]
    if net_credit <= 0:
        raise ValueError("NO_UNAMBIGUOUS_SOL_CREDIT")
    with localcontext() as context:
        context.prec = 100
        tokens = Decimal(sold).scaleb(-decimals)
        proceeds_per_token = Decimal(net_credit).scaleb(-9) / tokens
    return {
        "signature": signature, "wallet": wallet, "mint": mint,
        "slot": balances["slot"], "block_time": balances["block_time"],
        "token_amount_raw": str(sold), "token_decimals": decimals,
        "token_amount": format(tokens, "f"),
        "native_credit_lamports": str(native_credit),
        "wsol_credit_lamports": str(balances["wsol_delta"]),
        "net_sol_credit_lamports": str(net_credit),
        "network_fee_lamports": str(balances["fee"]),
        "proceeds_per_token_sol": format(proceeds_per_token, "f"),
        "proceeds_basis": "net_cash_including_fees_and_account_refunds",
    }


def _parse_receipt_balances(receipt, signature, wallet, mint):
    if not wallet or not mint or mint == WSOL_MINT:
        raise ValueError("UNSUPPORTED_TRADE_IDENTITY")
    if receipt.get("version", "legacy") not in ("legacy", 0, 1):
        raise ValueError("UNSUPPORTED_TRANSACTION_VERSION")
    transaction = receipt["transaction"]
    if transaction["signatures"][0] != signature:
        raise ValueError("TRANSACTION_SIGNATURE_MISMATCH")
    meta = receipt["meta"]
    if meta["err"] is not None:
        raise ValueError("TRANSACTION_FAILED")
    keys = transaction["message"]["accountKeys"]
    # jsonParsed already includes lookup-table addresses in accountKeys.
    addresses = [key["pubkey"] for key in keys]
    if len(set(addresses)) != len(addresses):
        raise ValueError("DUPLICATE_ACCOUNT_KEYS")
    if addresses[0] != wallet or keys[0]["signer"] is not True:
        raise ValueError("UNEXPECTED_FEE_PAYER")
    before = meta["preBalances"]
    after = meta["postBalances"]
    if len(before) != len(keys) or len(after) != len(keys):
        raise ValueError("ACCOUNT_BALANCES_MISMATCH")
    before = [unsigned_integer(value) for value in before]
    after = [unsigned_integer(value) for value in after]

    def token_balances(name):
        entries = meta[name]
        if not isinstance(entries, list):
            raise ValueError("MISSING_TOKEN_BALANCES")
        result = {}
        for entry in entries:
            index = unsigned_integer(entry["accountIndex"])
            if index >= len(keys) or index in result:
                raise ValueError("INVALID_TOKEN_ACCOUNT_INDEX")
            amount = entry["uiTokenAmount"]["amount"]
            decimals = unsigned_integer(entry["uiTokenAmount"]["decimals"])
            if (not isinstance(amount, str) or not amount.isascii()
                    or not amount.isdigit() or decimals > 255):
                raise ValueError("INVALID_RAW_TOKEN_AMOUNT")
            if int(amount) > 2**64 - 1:
                raise ValueError("INVALID_RAW_TOKEN_AMOUNT")
            owner = entry["owner"]
            if not isinstance(owner, str) or not owner:
                raise ValueError("TOKEN_OWNER_MISSING")
            result[index] = (owner, entry["mint"], decimals, int(amount))
        return result

    pre = token_balances("preTokenBalances")
    post = token_balances("postTokenBalances")
    target_delta = 0
    wsol_delta = 0
    target_decimals = set()
    for index in pre.keys() | post.keys():
        old, new = pre.get(index), post.get(index)
        identity = (new or old)[:3]
        if old and new and old[:3] != new[:3]:
            raise ValueError("TOKEN_ACCOUNT_IDENTITY_CHANGED")
        if identity[0] != wallet:
            continue
        # Missing entries mean zero only for an account created/closed here.
        if old is None and before[index] != 0:
            raise ValueError("UNKNOWN_PRE_TOKEN_BALANCE")
        if new is None and after[index] != 0:
            raise ValueError("UNKNOWN_POST_TOKEN_BALANCE")
        delta = (new[3] if new else 0) - (old[3] if old else 0)
        if identity[1] == mint:
            target_decimals.add(identity[2])
            target_delta += delta
        elif identity[1] == WSOL_MINT:
            if identity[2] != 9:
                raise ValueError("INVALID_WSOL_DECIMALS")
            wsol_delta += delta
        elif delta:
            raise ValueError("UNSUPPORTED_MULTI_ASSET_TRANSACTION")
    fee = unsigned_integer(meta["fee"])
    return {
        "target_delta": target_delta,
        "target_decimals": target_decimals,
        "native_delta": after[0] - before[0],
        "wsol_delta": wsol_delta,
        "fee": fee,
        "slot": unsigned_integer(receipt["slot"]),
        "block_time": receipt.get("blockTime"),
    }
