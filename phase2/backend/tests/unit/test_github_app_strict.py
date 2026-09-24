"""Tests: GitHub integration is strictly GitHub App based.

Verifies:
1. "repo" OAuth scope is never requested.
2. PATs are not supported.
3. /user/repos is never called.
4. Installation repository endpoint is used.
5. Repository outside installation scope is rejected.
6. Installation token creation works with mocked GitHub App APIs.
7. Forbidden endpoint test passes.
8. Callback requires installation_id.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock

import pytest

async def dummy_audit(*args, **kwargs): pass


from app.db.models.connection import Connection, ConnectionStatus


# ── Helpers ──

def _make_github_connection(
    client_id: uuid.UUID | None = None,
    resource_id: str | None = None,
    installation_id: str = "12345",
    status: ConnectionStatus = ConnectionStatus.CONNECTED) -> Connection:
    return Connection(
        id=uuid.uuid4(),
        client_id=client_id or uuid.uuid4(),
        provider="github",
        status=status,
        scopes=["contents:read", "metadata:read"],
        secret_ref="test_secret_ref",
        connected_at=datetime.now(timezone.utc),
        resource_id=resource_id,
        provider_metadata={"installation_id": installation_id})


# ── 1. No "repo" scope requested ──

class TestNoRepoScopeRequested:
    def test_get_install_url_no_repo_scope_with_app_url(self):
        """When github_app_install_url is set, the URL must not contain
        'scope=repo' or any OAuth scope parameter."""
        from app.api.v1.oauth_handlers import GitHubAppHandler

        with patch("app.api.v1.oauth_handlers.get_settings") as mock_settings:
            settings = MagicMock()
            settings.github_app_install_url = "https://github.com/apps/test-app/installations/new"
            settings.github_client_id = "test-client-id"
            settings.github_oauth_redirect_uri = "http://localhost:8001/callback"
            mock_settings.return_value = settings

            handler = GitHubAppHandler()
            url = handler.get_install_url("test-state-123")

        assert "scope" not in url.lower()
        assert "repo" not in url.lower()
        assert "state=test-state-123" in url

    def test_get_install_url_no_repo_scope_fallback(self):
        """When github_app_install_url is empty, it raises an OAuthError."""
        from app.api.v1.oauth_handlers import GitHubAppHandler, OAuthError

        with patch("app.api.v1.oauth_handlers.get_settings") as mock_settings:
            settings = MagicMock()
            settings.github_app_install_url = ""
            mock_settings.return_value = settings

            handler = GitHubAppHandler()
            with pytest.raises(OAuthError, match="GITHUB_APP_INSTALL_URL must be configured"):
                handler.get_install_url("test-state-456")

    def test_get_install_url_uses_app_install_url_when_configured(self):
        """When github_app_install_url is configured, it should be used
        instead of the OAuth authorize endpoint."""
        from app.api.v1.oauth_handlers import GitHubAppHandler

        with patch("app.api.v1.oauth_handlers.get_settings") as mock_settings:
            settings = MagicMock()
            settings.github_app_install_url = "https://github.com/apps/my-app/installations/new"
            mock_settings.return_value = settings

            handler = GitHubAppHandler()
            url = handler.get_install_url("state-abc")

        assert url.startswith("https://github.com/apps/my-app/installations/new")
        assert "login/oauth/authorize" not in url


# ── 2. PAT not supported ──

class TestPATNotSupported:
    @pytest.mark.asyncio
    async def test_store_tokens_does_not_store_access_token(self):
        """store_tokens_securely must NOT store access_token in the secret.
        Only installation_id is stored."""
        import json
        from app.api.v1.oauth_handlers import GitHubAppHandler

        stored_secrets = []

        async def mock_store(client_id, key, value):
            stored_secrets.append((client_id, key, json.loads(value)))
            return f"ref-{client_id}"

        with patch("app.api.v1.oauth_handlers.get_settings") as mock_settings:
            settings = MagicMock()
            mock_settings.return_value = settings

            with patch("app.api.v1.oauth_handlers.get_secret_manager") as mock_gsm:
                mock_sm = AsyncMock()
                mock_sm.store_secret = mock_store
                mock_gsm.return_value = mock_sm

                handler = GitHubAppHandler()

                await handler.store_tokens_securely(
                        "client-123",
                        installation_id="99999")

        assert len(stored_secrets) == 1
        _, key, data = stored_secrets[0]
        assert key == "github_app_token"
        assert data["installation_id"] == "99999"
        assert "access_token" not in data


# ── 3. /user/repos is never called ──

class TestUserReposNotCalled:
    def test_fetch_github_repos_no_user_repos_api_call(self):
        """The fetch_github_repos function body must not call /user/repos.
        The docstring may mention it for documentation purposes."""
        import inspect
        import textwrap
        from app.api.v1.resource_fetcher import fetch_github_repos

        source = inspect.getsource(fetch_github_repos)
        # Strip the docstring to only check the function body
        body_start = source.find('"""', source.find('"""') + 3) + 3
        body = source[body_start:]
        assert "/user/repos" not in body
        assert "github_fetch_user_repos" not in body

    def test_fetch_github_repos_no_repo_scope_in_error_messages(self):
        """Error messages must not reference 'repo' scope."""
        import inspect
        from app.api.v1.resource_fetcher import fetch_github_repos

        source = inspect.getsource(fetch_github_repos)
        # The old error message was "ensure the token has 'repo' scope"
        assert "'repo' scope" not in source


# ── 4. Installation repository endpoint is used ──

class TestInstallationReposUsed:
    def test_fetch_github_repos_uses_installation_endpoint(self):
        """fetch_github_repos must use /installation/repositories."""
        import inspect
        from app.api.v1.resource_fetcher import fetch_github_repos

        source = inspect.getsource(fetch_github_repos)
        assert "installation/repositories" in source

    @pytest.mark.asyncio
    async def test_fetch_github_repos_calls_installation_endpoint(self):
        """When called, fetch_github_repos must call /installation/repositories."""
        from app.api.v1.resource_fetcher import fetch_github_repos

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value={
            "repositories": [
                {
                    "full_name": "org/repo-1",
                    "html_url": "https://github.com/org/repo-1",
                    "private": False,
                    "default_branch": "main",
                },
            ]
        })

        with patch("app.api.v1.resource_fetcher.AuditedHTTPClient") as MockHTTP:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            MockHTTP.return_value = mock_ctx

            repos = await fetch_github_repos("inst-token-123", installation_id="42")

        # Verify it called the correct endpoint
        mock_client.get.assert_called_once()
        call_url = mock_client.get.call_args[0][0]
        assert "installation/repositories" in call_url
        assert "user/repos" not in call_url

        assert len(repos) == 1
        assert repos[0]["id"] == "org/repo-1"
        assert repos[0]["metadata"]["installation_id"] == "42"


# ── 5. Repository outside installation scope is rejected ──

class TestRepoOutsideInstallationRejected:
    def test_select_resource_validates_scope_concept(self):
        """The integrations.py select_resource endpoint must contain
        scope validation logic for GitHub."""
        import inspect
        from app.api.v1.integrations import select_resource

        source = inspect.getsource(select_resource)
        # Must check if the repo is accessible to the installation
        assert "accessible_repo_ids" in source or "installation_repos" in source
        assert "403" in source  # Returns 403 for out-of-scope repos

    def test_select_resource_contains_scope_rejection_message(self):
        """The error message for out-of-scope repos is clear."""
        import inspect
        from app.api.v1.integrations import select_resource

        source = inspect.getsource(select_resource)
        assert "not accessible to the installed GitHub App" in source


# ── 6. Installation token creation works ──

class TestInstallationTokenCreation:
    @pytest.mark.asyncio
    async def test_get_installation_token_calls_correct_endpoint(self):
        """get_installation_token must call POST /app/installations/{id}/access_tokens."""
        from app.api.v1.oauth_handlers import GitHubAppHandler

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value={
            "token": "ghs_test_installation_token_123",
            "expires_at": "2026-09-18T18:00:00Z",
        })

        with patch("app.api.v1.oauth_handlers.get_settings") as mock_settings:
            settings = MagicMock()
            settings.github_app_id = "123456"
            settings.github_app_private_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."  # dummy
            mock_settings.return_value = settings

            handler = GitHubAppHandler()

            with patch.object(handler, "_build_jwt", return_value="fake-jwt"):
                with patch("app.api.v1.oauth_handlers.AuditedHTTPClient") as MockHTTP:
                    mock_ctx = AsyncMock()
                    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_ctx.__aexit__ = AsyncMock(return_value=False)
                    MockHTTP.return_value = mock_ctx

                    result = await handler.get_installation_token("42")

        mock_client.post.assert_called_once()
        call_url = mock_client.post.call_args[0][0]
        assert "/app/installations/42/access_tokens" in call_url
        assert result["token"] == "ghs_test_installation_token_123"


# ── 7. Forbidden endpoints test ──

class TestForbiddenEndpoints:
    def test_github_adapter_has_forbidden_endpoints(self):
        """GitHubAdapter must define forbidden endpoints for write operations."""
        from app.integrations.github_adapter.adapter import GitHubAdapter

        adapter = GitHubAdapter()
        forbidden = adapter.forbidden_endpoints

        assert len(forbidden) > 0
        # Must block secrets endpoints
        assert any("secrets" in ep for ep in forbidden)
        # Must block write endpoints
        assert any("contents" in ep for ep in forbidden)
        assert any("hooks" in ep for ep in forbidden)

    def test_github_adapter_never_allows_write_operations(self):
        """The adapter's forbidden list must include all write-capable endpoints."""
        from app.integrations.github_adapter.adapter import GITHUB_FORBIDDEN_ENDPOINTS

        write_endpoints = ["/repos/*/contents", "/repos/*/merges", "/repos/*/dispatches"]
        for ep in write_endpoints:
            assert any(ep in fp for fp in GITHUB_FORBIDDEN_ENDPOINTS), f"Missing forbidden endpoint: {ep}"


# ── 8. Callback requires installation_id ──

class TestCallbackRequiresInstallationId:
    def test_callback_flow_stores_installation_id(self):
        """The GitHub callback must store installation_id in Connection metadata."""
        import inspect
        from app.api.v1.integrations import github_oauth_callback

        source = inspect.getsource(github_oauth_callback)
        # Must reference installation_id in metadata storage
        assert "installation_id" in source
        assert "provider_metadata" in source

    def test_callback_requires_installation_id(self):
        """The GitHub callback must require an installation_id."""
        import inspect
        from app.api.v1.integrations import github_oauth_callback

        source = inspect.getsource(github_oauth_callback)
        assert "if not installation_id:" in source

    def test_no_resource_listing_uses_user_token_fallback(self):
        """Resource listing for GitHub must NOT fall back to user token."""
        import inspect
        from app.api.v1.integrations import list_resources

        source = inspect.getsource(list_resources)
        # Ensure there's no fallback to user access_token for github repos
        github_section_start = source.find("provider == \"github\"")
        assert github_section_start > 0

        github_section = source[github_section_start:github_section_start + 2000]
        # Should NOT contain fallback to access_token for repo fetching
        assert "elif access_token:" not in github_section
        assert "Fallback to user token" not in github_section


# ── 9. GitHub adapter properties ──

class TestGitHubAdapterProperties:
    def test_adapter_id_is_github(self):
        """GitHubAdapter.id must be 'github'."""
        from app.integrations.github_adapter.adapter import GitHubAdapter
        adapter = GitHubAdapter()
        assert adapter.id == "github"
        assert adapter.provider == "github"

    def test_adapter_required_scopes_no_repo(self):
        """GitHubAdapter.required_scopes must NOT contain 'repo'."""
        from app.integrations.github_adapter.adapter import GitHubAdapter
        adapter = GitHubAdapter()
        for scope in adapter.required_scopes:
            assert scope != "repo", "Adapter must not require 'repo' OAuth scope"

    def test_adapter_is_registered(self):
        """GitHubAdapter must be registered in the adapter registry."""
        from app.integrations.registry import get_adapter
        adapter = get_adapter("github")
        assert adapter is not None
        assert adapter.id == "github"
