import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import load_test


class _FakeResponse:
    def __init__(self, status=200, payload=b"ok"):
        self.status = status
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class LoadTestScriptTests(unittest.TestCase):
    def test_parse_duration_value_supports_minutes_suffix(self):
        self.assertEqual(load_test.parse_duration_value("10m"), 600.0)

    @patch("scripts.load_test.ssl._create_unverified_context")
    def test_build_ssl_context_supports_insecure_mode(self, mock_unverified_context):
        insecure_context = object()
        mock_unverified_context.return_value = insecure_context

        context = load_test.build_ssl_context(insecure=True, ca_file=None)

        self.assertIs(context, insecure_context)
        mock_unverified_context.assert_called_once_with()

    @patch("scripts.load_test.ssl.create_default_context")
    def test_build_ssl_context_supports_custom_ca_bundle(self, mock_create_default_context):
        verified_context = MagicMock()
        mock_create_default_context.return_value = verified_context

        context = load_test.build_ssl_context(insecure=False, ca_file="/tmp/custom.pem")

        self.assertIs(context, verified_context)
        mock_create_default_context.assert_called_once_with(cafile="/tmp/custom.pem")

    @patch("scripts.load_test.urlopen")
    def test_run_load_test_collects_successful_requests(self, mock_urlopen):
        mock_urlopen.side_effect = [_FakeResponse() for _ in range(6)]

        results = load_test.run_load_test(
            "https://example.com",
            total_requests=6,
            concurrency=3,
            timeout=5.0,
            method="GET",
            headers={"User-Agent": "epm-load-test"},
            body=None,
        )

        self.assertEqual(len(results), 6)
        self.assertTrue(all(result.ok for result in results))
        self.assertTrue(all(result.status_code == 200 for result in results))
        self.assertTrue(all(result.error is None for result in results))
        self.assertEqual(mock_urlopen.call_count, 6)

    @patch("scripts.load_test.perform_request")
    def test_run_load_test_for_duration_keeps_sending_requests_until_time_expires(self, mock_perform_request):
        mock_perform_request.return_value = load_test.RequestResult(
            ok=True,
            status_code=200,
            latency_ms=5.0,
            error=None,
        )

        results = load_test.run_load_test_for_duration(
            "https://example.com",
            duration_seconds=0.02,
            concurrency=2,
            timeout=5.0,
            method="GET",
            headers={"User-Agent": "epm-load-test"},
            body=None,
        )

        self.assertGreater(len(results), 2)
        self.assertTrue(all(result.ok for result in results))
        self.assertTrue(mock_perform_request.called)

    def test_summarize_results_computes_status_and_latency_metrics(self):
        results = [
            load_test.RequestResult(ok=True, status_code=200, latency_ms=120.0, error=None),
            load_test.RequestResult(ok=True, status_code=200, latency_ms=80.0, error=None),
            load_test.RequestResult(ok=False, status_code=None, latency_ms=250.0, error="timeout"),
        ]

        summary = load_test.summarize_results(results, elapsed_seconds=2.0)

        self.assertEqual(summary["total_requests"], 3)
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["status_codes"], {200: 2})
        self.assertEqual(summary["errors"], {"timeout": 1})
        self.assertAlmostEqual(summary["requests_per_second"], 1.5)
        self.assertEqual(summary["latency_ms"]["min"], 80.0)
        self.assertEqual(summary["latency_ms"]["max"], 250.0)
        self.assertEqual(summary["latency_ms"]["p50"], 120.0)
        self.assertEqual(summary["latency_ms"]["p95"], 250.0)


if __name__ == "__main__":
    unittest.main()
