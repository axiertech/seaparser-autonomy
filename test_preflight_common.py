"""Offline tests for preflight.common."""
import json
import os
import resource
import unittest
from io import StringIO
from unittest.mock import patch

from preflight.common import CheckResult, emit, run, set_limits


class RunTests(unittest.TestCase):
    """run() catches exceptions, wraps returns and enforces budgets."""

    def test_ok_result(self):
        result = run("test", lambda: {"key": "value"})
        self.assertTrue(result.ok)
        self.assertEqual(result.details, {"key": "value"})
        self.assertIsNone(result.error)
        self.assertGreaterEqual(result.seconds, 0)

    def test_none_return_yields_no_details(self):
        result = run("test", lambda: None)
        self.assertTrue(result.ok)
        self.assertIsNone(result.details)

    def test_scalar_return_wrapped_in_value(self):
        result = run("test", lambda: 42)
        self.assertTrue(result.ok)
        self.assertEqual(result.details, {"value": 42})

    def test_exception_caught(self):
        def boom():
            raise RuntimeError("boom")
        result = run("test", boom)
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.error)
        self.assertIn("boom", result.error)

    def test_budget_exceeded_marks_failed(self):
        # 1 MiB is below any Python process RSS
        result = run("test", lambda: None, budget_mib=1)
        self.assertFalse(result.ok)
        self.assertIn("rss_budget_exceeded", result.error)


class EmitTests(unittest.TestCase):
    """emit() writes a stable one-line JSON payload."""

    def test_ok_payload(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            emit(CheckResult(name="x", ok=True, seconds=1.5,
                             peak_rss_kib=100, details={"k": "v"}))
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["name"], "x")
        self.assertEqual(payload["details"], {"k": "v"})
        self.assertNotIn("error", payload)

    def test_failed_payload(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            emit(CheckResult(name="x", ok=False, seconds=1.5,
                             error="boom"))
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "boom")

    def test_payload_is_single_line(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            emit(CheckResult(name="x", ok=True, seconds=1.5))
        self.assertEqual(buf.getvalue().count("\n"), 1)


class SetLimitsTests(unittest.TestCase):
    """set_limits applies env vars and RLIMIT_AS without crashing."""

    def test_sets_omp_env_when_absent(self):
        old_omp = os.environ.pop("OMP_NUM_THREADS", None)
        old_blas = os.environ.pop("OPENBLAS_NUM_THREADS", None)
        old_limit = resource.getrlimit(resource.RLIMIT_AS)
        try:
            set_limits(budget_mib=4096)
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "1")
            self.assertEqual(os.environ["OPENBLAS_NUM_THREADS"], "1")
        finally:
            resource.setrlimit(resource.RLIMIT_AS, old_limit)
            if old_omp is None:
                os.environ.pop("OMP_NUM_THREADS", None)
            else:
                os.environ["OMP_NUM_THREADS"] = old_omp
            if old_blas is None:
                os.environ.pop("OPENBLAS_NUM_THREADS", None)
            else:
                os.environ["OPENBLAS_NUM_THREADS"] = old_blas

    def test_does_not_override_existing_env(self):
        old_omp = os.environ.get("OMP_NUM_THREADS")
        old_limit = resource.getrlimit(resource.RLIMIT_AS)
        os.environ["OMP_NUM_THREADS"] = "4"
        try:
            set_limits(budget_mib=4096)
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "4")
        finally:
            resource.setrlimit(resource.RLIMIT_AS, old_limit)
            if old_omp is None:
                os.environ.pop("OMP_NUM_THREADS", None)
            else:
                os.environ["OMP_NUM_THREADS"] = old_omp

    def test_negative_budget_raises(self):
        with self.assertRaises(ValueError):
            set_limits(budget_mib=-1)


if __name__ == "__main__":
    unittest.main()
