import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DatabaseInitializationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "pump.db"
        db_patch = patch.object(app, "DB", self.path)
        db_patch.start()
        self.addCleanup(db_patch.stop)

    def test_repeated_connections_initialize_once(self):
        with patch.object(app, "_initialize_db", wraps=app._initialize_db) as initialize:
            for _ in range(3):
                conn = app.db()
                self.assertIsNotNone(conn.execute("SELECT 1 FROM trades LIMIT 1"))
                conn.close()
        self.assertEqual(initialize.call_count, 1)

    def test_concurrent_first_connections_initialize_once(self):
        def connect():
            conn = app.db()
            try:
                return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            finally:
                conn.close()

        with (
            patch.object(app, "_initialize_db", wraps=app._initialize_db) as initialize,
            concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor,
        ):
            self.assertEqual(list(executor.map(lambda _: connect(), range(4))), [0] * 4)
        self.assertEqual(initialize.call_count, 1)

    def test_replaced_database_is_initialized_again(self):
        conn = app.db()
        conn.close()
        self.path.unlink()
        with patch.object(app, "_initialize_db", wraps=app._initialize_db) as initialize:
            conn = app.db()
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0], 0)
            finally:
                conn.close()
        self.assertEqual(initialize.call_count, 1)


if __name__ == "__main__":
    unittest.main()
