import ast
import inspect
import textwrap
import unittest
from unittest.mock import patch

import app


class DemoRouteGuardsTests(unittest.TestCase):
    def test_every_demo_route_requires_debug_mode(self):
        for route in app.app.routes:
            if not route.path.startswith("/api/demo"):
                continue
            source = ast.parse(textwrap.dedent(inspect.getsource(route.endpoint)))
            calls = {
                node.func.id for node in ast.walk(source)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            with self.subTest(path=route.path):
                self.assertIn("auth", calls)
                self.assertIn("require_debug_mode", calls)

    def test_controlled_retry_is_unavailable_without_debug_mode(self):
        with (
            patch.object(app, "APP_TOKEN", "token"),
            patch.object(app, "DEBUG_MODE", False),
            patch.object(app, "create_execution_order") as create_order,
        ):
            with self.assertRaises(app.HTTPException) as raised:
                app.demo_controlled_retry("token")

        self.assertEqual(raised.exception.status_code, 404)
        create_order.assert_not_called()


if __name__ == "__main__":
    unittest.main()
