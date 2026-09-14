"""Safe planning and HTTP helpers for Helius webhook address updates."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


HELIUS_API_BASE_URL = "https://api-mainnet.helius-rpc.com"
HELIUS_MAX_WEBHOOK_ADDRESSES = 100000


class HeliusWebhookSyncError(RuntimeError):
    """A webhook configuration request failed or returned invalid data."""


def _http_error_retry_after_seconds(error):
    raw_value = str((error.headers or {}).get("Retry-After") or "").strip()
    try:
        value = float(raw_value)
    except ValueError:
        return None
    if value < 0:
        return None
    return int(value) if value.is_integer() else value


def _http_error_detail(error, secrets=()):
    try:
        body = error.read(4096)
    except Exception:
        return None
    if not body:
        return None

    text = body.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        for key in ("error", "message", "detail", "details"):
            value = parsed.get(key)
            if value:
                text = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, separators=(",", ":"))
                )
                break

    text = " ".join(str(text).split())
    for secret in secrets:
        secret = str(secret or "").strip()
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:300] or None


def _http_error_message(error, secrets=()):
    parts = [f"HELIUS_WEBHOOK_HTTP_{error.code}"]
    retry_after = _http_error_retry_after_seconds(error)
    if retry_after is not None:
        parts.append(f"retry_after_seconds={retry_after}")
    detail = _http_error_detail(error, secrets=secrets)
    if detail:
        parts.append(f"detail={detail}")
    return "|".join(parts)


def normalize_addresses(addresses):
    """Return unique, non-empty addresses in deterministic order."""
    return sorted({
        str(address).strip()
        for address in (addresses or ())
        if str(address or "").strip()
    })


def webhook_account_addresses(webhook):
    """Read a complete address list or fail closed before any replacement."""
    if not isinstance(webhook, dict):
        raise HeliusWebhookSyncError("INVALID_HELIUS_WEBHOOK_CONFIG")
    addresses = webhook.get("accountAddresses")
    if not isinstance(addresses, list):
        raise HeliusWebhookSyncError("HELIUS_WEBHOOK_ADDRESSES_MISSING")
    normalized = normalize_addresses(addresses)
    if len(normalized) > HELIUS_MAX_WEBHOOK_ADDRESSES:
        raise HeliusWebhookSyncError("HELIUS_WEBHOOK_ADDRESS_LIMIT_EXCEEDED")
    return normalized


def plan_webhook_address_sync(
    remote_addresses,
    tracked_tokens,
    managed_tokens=(),
    pending_tokens=(),
):
    """Plan token changes without taking ownership of remote base addresses.

    ``pending_tokens`` closes the crash window between a successful remote
    update and recording that success locally. On restart, those addresses are
    still treated as ours instead of being absorbed into the permanent base.
    """
    remote = set(normalize_addresses(remote_addresses))
    tracked = set(normalize_addresses(tracked_tokens))
    owned = set(normalize_addresses(managed_tokens))
    owned.update(normalize_addresses(pending_tokens))

    # La lectura remota es autoridad para las direcciones ajenas. Si alguien
    # elimina manualmente una wallet base, no debemos resucitarla desde cache.
    base = remote - owned
    desired = base | tracked

    return {
        "base_addresses": sorted(base),
        "tracked_tokens": sorted(tracked),
        "remote_addresses": sorted(remote),
        "desired_addresses": sorted(desired),
        "additions": sorted(desired - remote),
        "removals": sorted(remote - desired),
    }


def build_webhook_update_payload(webhook, desired_addresses):
    """Preserve remote webhook fields while replacing only its addresses."""
    if not isinstance(webhook, dict):
        raise HeliusWebhookSyncError("INVALID_HELIUS_WEBHOOK_CONFIG")

    webhook_url = str(webhook.get("webhookURL") or "").strip()
    webhook_type = str(webhook.get("webhookType") or "").strip()
    if not webhook_url or not webhook_type:
        raise HeliusWebhookSyncError("INCOMPLETE_HELIUS_WEBHOOK_CONFIG")

    desired = normalize_addresses(desired_addresses)
    if len(desired) > HELIUS_MAX_WEBHOOK_ADDRESSES:
        raise HeliusWebhookSyncError("HELIUS_WEBHOOK_ADDRESS_LIMIT_EXCEEDED")

    payload = {
        "webhookURL": webhook_url,
        "webhookType": webhook_type,
        "accountAddresses": desired,
    }
    for key in (
        "transactionTypes",
        "authHeader",
        "encoding",
        "txnStatus",
    ):
        if key in webhook and webhook[key] is not None:
            payload[key] = webhook[key]

    return payload


def _webhook_url(api_key, webhook_id, api_base_url):
    api_key = str(api_key or "").strip()
    webhook_id = str(webhook_id or "").strip()
    if not api_key or not webhook_id:
        raise HeliusWebhookSyncError("HELIUS_WEBHOOK_SYNC_NOT_CONFIGURED")

    query = urlencode({"api-key": api_key})
    return (
        f"{str(api_base_url).rstrip('/')}/v0/webhooks/"
        f"{quote(webhook_id, safe='')}?{query}"
    )


def _request_json(
    api_key,
    webhook_id,
    method,
    payload=None,
    timeout=15,
    api_base_url=HELIUS_API_BASE_URL,
):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        _webhook_url(api_key, webhook_id, api_base_url),
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        sensitive_values = [api_key, webhook_id]
        if isinstance(payload, dict):
            sensitive_values.append(payload.get("authHeader"))
        raise HeliusWebhookSyncError(_http_error_message(
            exc,
            secrets=sensitive_values,
        )) from exc
    except (TimeoutError, URLError, json.JSONDecodeError) as exc:
        raise HeliusWebhookSyncError(
            f"HELIUS_WEBHOOK_REQUEST_FAILED:{exc.__class__.__name__}"
        ) from exc

    if not isinstance(result, dict):
        raise HeliusWebhookSyncError("INVALID_HELIUS_WEBHOOK_RESPONSE")
    returned_id = str(result.get("webhookID") or "").strip()
    if returned_id and returned_id != str(webhook_id).strip():
        raise HeliusWebhookSyncError("HELIUS_WEBHOOK_ID_MISMATCH")
    return result


def fetch_helius_webhook(api_key, webhook_id, timeout=15):
    """Fetch the authoritative remote webhook configuration."""
    result = _request_json(
        api_key,
        webhook_id,
        "GET",
        timeout=timeout,
    )
    webhook_account_addresses(result)
    return result


def update_helius_webhook_addresses(
    api_key,
    webhook_id,
    webhook,
    desired_addresses,
    timeout=15,
):
    """Replace addresses while preserving all supported remote settings."""
    desired = normalize_addresses(desired_addresses)
    payload = build_webhook_update_payload(webhook, desired)
    result = _request_json(
        api_key,
        webhook_id,
        "PUT",
        payload=payload,
        timeout=timeout,
    )
    if webhook_account_addresses(result) != desired:
        raise HeliusWebhookSyncError("HELIUS_WEBHOOK_UPDATE_NOT_CONFIRMED")
    return result
