import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HeliusCreditUsageError(Exception):
    pass


def fetch_helius_credit_usage(api_key, project_id, *, timeout=10):
    if not api_key:
        raise HeliusCreditUsageError("HELIUS_API_KEY_MISSING")
    try:
        project_uuid = str(uuid.UUID(project_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HeliusCreditUsageError("HELIUS_PROJECT_ID_INVALID") from exc

    request = Request(
        f"https://admin-api.helius.xyz/v0/admin/projects/{project_uuid}/usage",
        headers={"X-Api-Key": api_key, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise HeliusCreditUsageError(f"HELIUS_ADMIN_HTTP_{exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HeliusCreditUsageError("HELIUS_ADMIN_UNAVAILABLE") from exc
    except (ValueError, UnicodeError) as exc:
        raise HeliusCreditUsageError("HELIUS_ADMIN_INVALID_JSON") from exc

    if not isinstance(payload, dict):
        raise HeliusCreditUsageError("HELIUS_ADMIN_INVALID_RESPONSE")
    details = payload.get("subscriptionDetails")
    cycle = details.get("billingCycle") if isinstance(details, dict) else None
    usage = payload.get("usage")
    return {
        "credits_remaining": payload.get("creditsRemaining"),
        "prepaid_credits_remaining": payload.get("prepaidCreditsRemaining"),
        "credits_used": payload.get("creditsUsed"),
        "credit_limit": details.get("creditsLimit") if isinstance(details, dict) else None,
        "plan": details.get("plan") if isinstance(details, dict) else None,
        "billing_cycle": cycle if isinstance(cycle, dict) else None,
        "usage": usage if isinstance(usage, dict) else None,
    }
