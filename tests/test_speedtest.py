"""Tests that do not need internet access: a local HTTP server is enough."""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import speedtest  # noqa: E402

PAYLOAD = b"x" * (256 * 1024)  # 256 KiB


class Handler(BaseHTTPRequestHandler):
    """Serve PAYLOAD on /ok and 404 on everything else."""

    def do_GET(self):  # noqa: N802 - name required by http.server
        if self.path.startswith("/ok"):
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args):  # keep test output clean
        pass


class SpeedtestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_counts_every_byte_of_every_request(self):
        results = [speedtest.fetch_once(f"{self.base}/ok", 5.0) for _ in range(3)]
        self.assertTrue(all(r["ok"] for r in results))
        self.assertTrue(all(r["status"] == 200 for r in results))
        self.assertTrue(all(r["bytes"] == len(PAYLOAD) for r in results))

        stats = speedtest.summarize(results)
        self.assertEqual(stats["succeeded"], 3)
        self.assertEqual(stats["total_bytes"], 3 * len(PAYLOAD))
        self.assertGreater(stats["avg_speed_bps"], 0)
        self.assertAlmostEqual(
            stats["effective_speed_bps"],
            stats["total_bytes"] * 8 / stats["total_seconds"],
        )

    def test_http_error_is_reported_not_raised(self):
        result = speedtest.fetch_once(f"{self.base}/missing", 5.0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["bytes"], 0)
        self.assertIn("HTTP Error 404", result["error"])

    def test_summary_without_successes_is_safe(self):
        stats = speedtest.summarize([speedtest.fetch_once(f"{self.base}/missing", 5.0)])
        self.assertEqual(stats["succeeded"], 0)
        self.assertEqual(stats["avg_speed_bps"], 0.0)
        self.assertEqual(stats["effective_speed_bps"], 0.0)

    def test_cli_returns_error_code_when_everything_fails(self):
        code = speedtest.main([f"{self.base}/missing", "-n", "1", "--timeout", "5"])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
