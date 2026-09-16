import json
from urllib.parse import quote


def build_helius_standard_wss_url(api_key, explicit_url=""):
    explicit_url = str(explicit_url or "").strip()
    if explicit_url:
        if not explicit_url.startswith(("wss://", "ws://")):
            raise ValueError("HELIUS_STANDARD_WSS_URL_INVALID")
        return explicit_url

    api_key = str(api_key or "").strip()
    if not api_key:
        return ""
    return (
        "wss://mainnet.helius-rpc.com/?api-key="
        + quote(api_key, safe="")
    )


def build_logs_subscribe_request(request_id, wallet):
    wallet = str(wallet or "").strip()
    if not wallet:
        raise ValueError("HELIUS_STANDARD_WSS_WALLET_REQUIRED")
    return {
        "jsonrpc": "2.0",
        "id": int(request_id),
        "method": "logsSubscribe",
        "params": [
            {"mentions": [wallet]},
            {"commitment": "confirmed"},
        ],
    }


def decode_wss_message(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    payload = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise ValueError("HELIUS_STANDARD_WSS_MESSAGE_INVALID")
    return payload


def subscription_confirmation(payload, pending_requests):
    if "id" not in payload or "result" not in payload:
        return None
    try:
        request_id = int(payload["id"])
        subscription_id = int(payload["result"])
    except (TypeError, ValueError):
        return None
    wallet = pending_requests.get(request_id)
    if not wallet:
        return None
    return subscription_id, wallet


def parse_logs_notification(payload, subscriptions):
    if payload.get("method") != "logsNotification":
        return None
    params = payload.get("params")
    if not isinstance(params, dict):
        return None
    try:
        subscription_id = int(params.get("subscription"))
    except (TypeError, ValueError):
        return None
    wallet = subscriptions.get(subscription_id)
    result = params.get("result")
    if not wallet or not isinstance(result, dict):
        return None
    value = result.get("value")
    context = result.get("context")
    if not isinstance(value, dict) or not isinstance(context, dict):
        return None
    signature = str(value.get("signature") or "").strip()
    logs = value.get("logs") or []
    if not signature or not isinstance(logs, list):
        return None
    try:
        slot = int(context.get("slot"))
    except (TypeError, ValueError):
        slot = None
    return {
        "wallet": wallet,
        "signature": signature,
        "slot": slot,
        "failed": value.get("err") is not None,
        "logs": [line for line in logs if isinstance(line, str)],
    }


def invokes_program(logs, program_ids):
    prefixes = tuple(
        f"Program {program_id} invoke "
        for program_id in program_ids
        if program_id
    )
    return bool(prefixes) and any(
        line.startswith(prefixes)
        for line in logs
        if isinstance(line, str)
    )
