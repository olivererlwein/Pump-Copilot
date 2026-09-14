import json
import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import AsyncMock, Mock, patch

import app
import helius_webhook_sync


BASE_ADDRESS = "base-wallet"
TOKEN_ADDRESS = "tracked-token"


def webhook(addresses):
    return {
        "webhookID": "webhook-id",
        "webhookURL": "https://example.test/api/helius-webhook",
        "webhookType": "raw",
        "accountAddresses": list(addresses),
        "transactionTypes": ["ANY"],
        "authHeader": "secret",
        "encoding": "jsonParsed",
        "txnStatus": "success",
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, amount=-1):
        return json.dumps(self.payload).encode("utf-8")


class HeliusWebhookPlanningTests(unittest.TestCase):
    def test_http_error_preserves_safe_rate_limit_diagnostics(self):
        headers = Message()
        headers["Retry-After"] = "900"
        error = HTTPError(
            "https://helius.test",
            429,
            "Too Many Requests",
            headers,
            io.BytesIO(
                b'{"error":"monthly quota exhausted for api-key"}'
            ),
        )

        with patch.object(
            helius_webhook_sync,
            "urlopen",
            side_effect=error,
        ):
            with self.assertRaises(
                helius_webhook_sync.HeliusWebhookSyncError
            ) as raised:
                helius_webhook_sync.fetch_helius_webhook(
                    "api-key",
                    "webhook-id",
                )

        message = str(raised.exception)
        self.assertIn("HELIUS_WEBHOOK_HTTP_429", message)
        self.assertIn("retry_after_seconds=900", message)
        self.assertIn("monthly quota exhausted", message)
        self.assertNotIn("api-key", message)

    def test_update_error_redacts_preserved_auth_header(self):
        error = HTTPError(
            "https://helius.test",
            400,
            "Bad Request",
            Message(),
            io.BytesIO(b'{"error":"invalid auth value secret"}'),
        )

        with patch.object(
            helius_webhook_sync,
            "urlopen",
            side_effect=error,
        ):
            with self.assertRaises(
                helius_webhook_sync.HeliusWebhookSyncError
            ) as raised:
                helius_webhook_sync.update_helius_webhook_addresses(
                    "api-key",
                    "webhook-id",
                    webhook([BASE_ADDRESS]),
                    [BASE_ADDRESS, TOKEN_ADDRESS],
                )

        self.assertIn("invalid auth value [redacted]", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_preserves_remote_base_and_adds_tracked_tokens(self):
        plan = helius_webhook_sync.plan_webhook_address_sync(
            [BASE_ADDRESS],
            [TOKEN_ADDRESS],
        )

        self.assertEqual(plan["base_addresses"], [BASE_ADDRESS])
        self.assertEqual(
            plan["desired_addresses"],
            sorted([BASE_ADDRESS, TOKEN_ADDRESS]),
        )
        self.assertEqual(plan["additions"], [TOKEN_ADDRESS])
        self.assertEqual(plan["removals"], [])

    def test_removes_only_previously_managed_tokens(self):
        plan = helius_webhook_sync.plan_webhook_address_sync(
            [BASE_ADDRESS, TOKEN_ADDRESS],
            [],
            managed_tokens=[TOKEN_ADDRESS],
        )

        self.assertEqual(plan["desired_addresses"], [BASE_ADDRESS])
        self.assertEqual(plan["removals"], [TOKEN_ADDRESS])

    def test_pending_tokens_are_not_absorbed_after_crash(self):
        plan = helius_webhook_sync.plan_webhook_address_sync(
            [BASE_ADDRESS, TOKEN_ADDRESS],
            [],
            pending_tokens=[TOKEN_ADDRESS],
        )

        self.assertEqual(plan["base_addresses"], [BASE_ADDRESS])
        self.assertEqual(plan["removals"], [TOKEN_ADDRESS])

    def test_does_not_restore_a_base_address_removed_remotely(self):
        plan = helius_webhook_sync.plan_webhook_address_sync(
            [],
            [],
        )

        self.assertEqual(plan["base_addresses"], [])
        self.assertEqual(plan["desired_addresses"], [])

    def test_update_preserves_remote_configuration(self):
        remote = webhook([BASE_ADDRESS])
        response = webhook([BASE_ADDRESS, TOKEN_ADDRESS])

        with patch.object(
            helius_webhook_sync,
            "urlopen",
            return_value=FakeResponse(response),
        ) as request:
            result = helius_webhook_sync.update_helius_webhook_addresses(
                "api-key",
                "webhook-id",
                remote,
                [TOKEN_ADDRESS, BASE_ADDRESS],
            )

        sent_request = request.call_args.args[0]
        sent_payload = json.loads(sent_request.data.decode("utf-8"))
        self.assertEqual(sent_request.get_method(), "PUT")
        self.assertEqual(
            sent_payload["accountAddresses"],
            sorted([BASE_ADDRESS, TOKEN_ADDRESS]),
        )
        self.assertEqual(sent_payload["transactionTypes"], ["ANY"])
        self.assertEqual(sent_payload["authHeader"], "secret")
        self.assertEqual(result, response)

    def test_missing_remote_address_list_fails_closed(self):
        incomplete = webhook([])
        incomplete.pop("accountAddresses")

        with self.assertRaises(
            helius_webhook_sync.HeliusWebhookSyncError
        ):
            helius_webhook_sync.webhook_account_addresses(incomplete)

    def test_address_limit_is_enforced_before_request(self):
        too_many = [
            f"address-{index}"
            for index in range(
                helius_webhook_sync.HELIUS_MAX_WEBHOOK_ADDRESSES + 1
            )
        ]

        with self.assertRaises(
            helius_webhook_sync.HeliusWebhookSyncError
        ):
            helius_webhook_sync.build_webhook_update_payload(
                webhook([]),
                too_many,
            )


class HeliusWebhookSyncIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            app,
            "DB",
            Path(self.directory.name) / "sync.db",
        )
        self.db_patch.start()
        app.migrate_database()

    def tearDown(self):
        self.db_patch.stop()
        self.directory.cleanup()

    def sync_config(self, apply):
        patches = [
            patch.object(app, "HELIUS_WEBHOOK_SYNC_ENABLED", True),
            patch.object(app, "HELIUS_WEBHOOK_SYNC_APPLY", apply),
            patch.object(app, "HELIUS_WEBHOOK_ENABLED", True),
            patch.object(app, "HELIUS_WEBHOOK_SECRET", "secret"),
            patch.object(app, "HELIUS_API_KEY", "api-key"),
            patch.object(app, "HELIUS_WEBHOOK_ID", "webhook-id"),
            patch.object(app, "HELIUS_WEBHOOK_SYNC_AUDIT_SECONDS", 900),
            patch.object(app, "HELIUS_WEBHOOK_SYNC_ERROR_RETRY_SECONDS", 60),
        ]
        return patches

    def test_disabled_sync_never_reads_remote_configuration(self):
        fetch = Mock(side_effect=AssertionError("Unexpected Helius request"))
        with patch.object(app, "HELIUS_WEBHOOK_SYNC_ENABLED", False):
            result = app.sync_helius_webhook_tokens_once(
                now=100,
                fetch_webhook_fn=fetch,
            )

        self.assertEqual(result["status"], "disabled")
        fetch.assert_not_called()

    def test_dry_run_records_plan_without_updating_and_uses_cache(self):
        fetch = Mock(return_value=webhook([BASE_ADDRESS]))
        update = Mock(side_effect=AssertionError("Unexpected Helius update"))

        with patch.object(app, "TRACKED_TOKENS", {TOKEN_ADDRESS}):
            with self.enter_patches(self.sync_config(apply=False)):
                first = app.sync_helius_webhook_tokens_once(
                    now=100,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )
                second = app.sync_helius_webhook_tokens_once(
                    now=101,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )

        self.assertEqual(first["status"], "dry_run_changed")
        self.assertEqual(first["planned_additions"], 1)
        self.assertEqual(second["status"], "cached")
        fetch.assert_called_once()
        update.assert_not_called()

    def test_apply_adds_then_removes_only_managed_token(self):
        remote_addresses = [BASE_ADDRESS]

        def fetch(*args, **kwargs):
            return webhook(remote_addresses)

        def update(api_key, webhook_id, remote, desired, timeout):
            remote_addresses[:] = list(desired)
            return webhook(remote_addresses)

        tracked = {TOKEN_ADDRESS}
        with patch.object(app, "TRACKED_TOKENS", tracked):
            with self.enter_patches(self.sync_config(apply=True)):
                added = app.sync_helius_webhook_tokens_once(
                    now=100,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )
                tracked.clear()
                removed = app.sync_helius_webhook_tokens_once(
                    now=110,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )

        self.assertEqual(added["status"], "updated")
        self.assertEqual(removed["status"], "updated")
        self.assertEqual(remote_addresses, [BASE_ADDRESS])
        state = app.get_helius_webhook_sync_state()
        self.assertEqual(state["base_addresses"], [BASE_ADDRESS])
        self.assertEqual(state["managed_tokens"], [])
        self.assertEqual(state["updates"], 2)

    def test_remote_removal_of_base_address_becomes_authoritative(self):
        remote_addresses = [BASE_ADDRESS]
        fetch = Mock(side_effect=lambda *args, **kwargs: webhook(remote_addresses))
        update = Mock(side_effect=AssertionError("Unexpected Helius update"))

        with patch.object(app, "TRACKED_TOKENS", set()):
            with self.enter_patches(self.sync_config(apply=True)):
                first = app.sync_helius_webhook_tokens_once(
                    now=100,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )
                remote_addresses.clear()
                second = app.sync_helius_webhook_tokens_once(
                    now=1001,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )

        self.assertEqual(first["status"], "current")
        self.assertEqual(second["status"], "current")
        self.assertEqual(
            app.get_helius_webhook_sync_state()["base_addresses"],
            [],
        )
        self.assertEqual(fetch.call_count, 2)
        update.assert_not_called()

    def test_pending_marker_recovers_after_remote_success_and_local_failure(self):
        remote_addresses = [BASE_ADDRESS]
        first_attempt = True

        def fetch(*args, **kwargs):
            return webhook(remote_addresses)

        def update(api_key, webhook_id, remote, desired, timeout):
            nonlocal first_attempt
            remote_addresses[:] = list(desired)
            if first_attempt:
                first_attempt = False
                raise RuntimeError("connection lost after PUT")
            return webhook(remote_addresses)

        tracked = {TOKEN_ADDRESS}
        with patch.object(app, "TRACKED_TOKENS", tracked):
            with self.enter_patches(self.sync_config(apply=True)):
                failed = app.sync_helius_webhook_tokens_once(
                    now=100,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )
                self.assertEqual(failed["status"], "error")
                self.assertEqual(
                    app.get_helius_webhook_sync_state()["pending_tokens"],
                    [TOKEN_ADDRESS],
                )

                tracked.clear()
                backed_off = app.sync_helius_webhook_tokens_once(
                    now=110,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )
                recovered = app.sync_helius_webhook_tokens_once(
                    now=161,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )

        self.assertEqual(backed_off["status"], "error_backoff")
        self.assertEqual(recovered["status"], "updated")
        self.assertEqual(remote_addresses, [BASE_ADDRESS])
        self.assertEqual(
            app.get_helius_webhook_sync_state()["pending_tokens"],
            [],
        )

    def test_status_reports_stale_managed_token_when_remote_fetch_fails(self):
        remote_addresses = [BASE_ADDRESS]

        def fetch(*args, **kwargs):
            return webhook(remote_addresses)

        def update(api_key, webhook_id, remote, desired, timeout):
            remote_addresses[:] = list(desired)
            return webhook(remote_addresses)

        tracked = {TOKEN_ADDRESS}
        with patch.object(app, "TRACKED_TOKENS", tracked):
            with self.enter_patches(self.sync_config(apply=True)):
                added = app.sync_helius_webhook_tokens_once(
                    now=100,
                    fetch_webhook_fn=fetch,
                    update_webhook_fn=update,
                )
                tracked.clear()
                failed = app.sync_helius_webhook_tokens_once(
                    now=1000,
                    fetch_webhook_fn=Mock(
                        side_effect=helius_webhook_sync.HeliusWebhookSyncError(
                            "HELIUS_WEBHOOK_HTTP_429"
                        )
                    ),
                    update_webhook_fn=update,
                )

        self.assertEqual(added["status"], "updated")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["tracked_tokens"], 0)
        self.assertEqual(failed["managed_tokens"], 1)
        self.assertEqual(failed["planned_additions"], 0)
        self.assertEqual(failed["planned_removals"], 1)

    def test_rate_limit_uses_longer_backoff(self):
        fetch = Mock(side_effect=[
            helius_webhook_sync.HeliusWebhookSyncError(
                "HELIUS_WEBHOOK_HTTP_429|retry_after_seconds=900"
            ),
            webhook([BASE_ADDRESS]),
        ])

        with patch.object(app, "TRACKED_TOKENS", set()):
            with self.enter_patches(self.sync_config(apply=True)):
                with patch.object(
                    app, "HELIUS_WEBHOOK_SYNC_RATE_LIMIT_RETRY_SECONDS", 300
                ):
                    failed = app.sync_helius_webhook_tokens_once(
                        now=100,
                        fetch_webhook_fn=fetch,
                    )
                    backed_off = app.sync_helius_webhook_tokens_once(
                        now=401,
                        fetch_webhook_fn=fetch,
                    )
                    recovered = app.sync_helius_webhook_tokens_once(
                        now=1001,
                        fetch_webhook_fn=fetch,
                    )

        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["retry_seconds"], 900)
        self.assertEqual(failed["next_retry_ts"], 1000)
        self.assertEqual(backed_off["status"], "error_backoff")
        self.assertEqual(backed_off["next_retry_ts"], 1000)
        self.assertEqual(recovered["status"], "current")
        self.assertEqual(fetch.call_count, 2)

    def test_stats_do_not_expose_credentials(self):
        with self.enter_patches(self.sync_config(apply=False)):
            with patch.object(app, "APP_TOKEN", "app-token"):
                report = app.api_helius_webhook_stats("app-token")

        sync = report["webhook_sync"]
        self.assertTrue(sync["configured"])
        self.assertNotIn("api-key", json.dumps(sync))
        self.assertNotIn("webhook-id", json.dumps(sync))
        self.assertNotIn("secret", json.dumps(sync))

    def enter_patches(self, patches):
        class PatchGroup:
            def __enter__(self):
                for item in patches:
                    item.start()
                return self

            def __exit__(self, exc_type, exc, traceback):
                for item in reversed(patches):
                    item.stop()
                return False

        return PatchGroup()


class HeliusWebhookSyncAlertTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        app.HELIUS_WEBHOOK_SYNC_ALERT_ACTIVE = False

    async def test_alerts_once_during_failure_and_once_on_recovery(self):
        with patch.object(
            app, "DISCORD_ALERT_WEBHOOK_URL", "https://discord.test/webhook"
        ), patch.object(
            app, "send_discord_alert", new_callable=AsyncMock,
            return_value=True,
        ) as send_alert:
            await app.update_helius_webhook_sync_alert({
                "status": "error",
                "last_error": "HeliusWebhookSyncError:HELIUS_WEBHOOK_HTTP_429",
            })
            await app.update_helius_webhook_sync_alert({
                "status": "error_backoff",
                "last_error": "HeliusWebhookSyncError:HELIUS_WEBHOOK_HTTP_429",
            })
            await app.update_helius_webhook_sync_alert({
                "status": "current",
                "last_error": None,
            })

        self.assertEqual(send_alert.await_count, 2)
        self.assertIn("429", send_alert.await_args_list[0].args[0])
        self.assertIn("recovered", send_alert.await_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
