"""Unit and integration tests for authentication endpoints against running server.
"""

import pytest
import httpx


@pytest.mark.asyncio
async def test_otp_and_password_endpoints():
    base_url = "http://localhost:8001/api/v1/auth"
    test_email = "authtest_runner@example.com"

    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        # 1. Send Login OTP
        resp = await client.post("/otp/send-login", json={"email": test_email})
        assert resp.status_code in (200, 429), resp.text
        if resp.status_code == 200:
            assert "Verification code sent" in resp.json()["message"]

        # 2. Verify invalid OTP format / wrong code
        wrong_resp = await client.post(
            "/otp/verify-login",
            json={"email": test_email, "otp": "000000"}
        )
        assert wrong_resp.status_code == 400
        assert "verification code" in wrong_resp.json()["detail"].lower() or "incorrect" in wrong_resp.json()["detail"].lower()

        # 3. Forgot password OTP request
        forgot_resp = await client.post(
            "/forgot-password",
            json={"email": test_email}
        )
        assert forgot_resp.status_code in (200, 429), forgot_resp.text

        # 4. Short password on reset rejected
        short_resp = await client.post(
            "/reset-password",
            json={"email": test_email, "otp": "123456", "new_password": "123"}
        )
        assert short_resp.status_code == 400
        assert "at least 6 characters" in short_resp.json()["detail"].lower()
