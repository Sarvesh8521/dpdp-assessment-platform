"""Tests for the Real-Account Acceptance Test Harness

These tests verify that the harness itself operates securely and correctly.
It does NOT execute real external API calls; rather, it ensures the harness
framework handles credentials, outputs, and blocked configurations appropriately.
"""

import os
import subprocess
import sys
from pathlib import Path
import pytest

HARNESS_SCRIPT = Path(__file__).parent / "real_account_harness.py"


def test_harness_fails_without_token():
    """Verify the harness refuses to run if no ephemeral token is provided."""
    env = os.environ.copy()
    if "HARNESS_ACCESS_TOKEN" in env:
        del env["HARNESS_ACCESS_TOKEN"]

    result = subprocess.run(
        [sys.executable, str(HARNESS_SCRIPT), "--adapter", "gtm"],
        env=env,
        capture_output=True,
        text=True
    )

    assert result.returncode != 0
    assert "HARNESS_ACCESS_TOKEN environment variable is not set" in result.stdout


def test_harness_handles_unknown_adapter():
    """Verify the harness fails gracefully for an unknown adapter."""
    env = os.environ.copy()
    env["HARNESS_ACCESS_TOKEN"] = "fake_token_123"

    result = subprocess.run(
        [sys.executable, str(HARNESS_SCRIPT), "--adapter", "unknown_adapter"],
        env=env,
        capture_output=True,
        text=True
    )

    assert result.returncode != 0
    assert "Unknown integration" in result.stdout or "ERROR" in result.stdout


def test_harness_does_not_leak_token(monkeypatch):
    """Verify the harness output never contains the provided token."""
    env = os.environ.copy()
    secret_token = "SUPER_SECRET_TOKEN_DO_NOT_LEAK_9999"
    env["HARNESS_ACCESS_TOKEN"] = secret_token

    result = subprocess.run(
        [sys.executable, str(HARNESS_SCRIPT), "--adapter", "gtm"],
        env=env,
        capture_output=True,
        text=True
    )

    assert secret_token not in result.stdout
    assert secret_token not in result.stderr
