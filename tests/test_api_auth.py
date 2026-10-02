import asyncio
import unittest
from unittest.mock import patch

import app


class ApiAuthTests(unittest.TestCase):
    def test_invalid_configuration_fails_closed_before_startup_work(self):
        for token in ("", "  ", "change-this-long-random-token"):
            with self.subTest(token=bool(token)), patch.object(app, "APP_TOKEN", token):
                with self.assertRaises(RuntimeError):
                    asyncio.run(app.startup())
                with self.assertRaises(app.HTTPException) as raised:
                    app.auth(token)
                self.assertEqual(raised.exception.status_code, 401)

    def test_internal_training_routes_require_token(self):
        routes = (
            (app.api_training_stats, "get_training_dataset_stats", {}),
            (app.api_training_stats_by_trader, "get_training_stats_by_trader", {}),
            (app.api_training_checkpoint_freshness, "get_training_checkpoint_freshness", {}),
            (app.api_training_expired_preview, "get_training_expired_preview", {"limit": 20}),
            (app.api_training_dataset_preview, "get_training_dataset_rows", {"limit": 20}),
        )
        with patch.object(app, "APP_TOKEN", "test-token"):
            for endpoint, getter, kwargs in routes:
                with self.subTest(endpoint=endpoint.__name__), patch.object(app, getter, return_value=[]):
                    for token in ("", "wrong"):
                        with self.assertRaises(app.HTTPException) as raised:
                            endpoint(x_app_token=token, **kwargs)
                        self.assertEqual(raised.exception.status_code, 401)
                    endpoint(x_app_token="test-token", **kwargs)

    def test_expired_preview_clamps_limit(self):
        with patch.object(app, "APP_TOKEN", "test-token"), patch.object(
            app, "get_training_expired_preview", return_value=[]
        ) as getter:
            app.api_training_expired_preview(limit=-1, x_app_token="test-token")
            getter.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
