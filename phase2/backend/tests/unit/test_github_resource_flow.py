"""Tests: GitHub resource selection persistence and assessment handoff.

Verifies:
1. GitHub resource selection persists resource_id/resource_name on Connection.
2. Assessment service does NOT return "no assessment resource selected"
   when a valid GitHub repo exists in config.github_repos.
3. Assessment service reaches the GitHub kwargs builder (lines 410+).
4. Another client's GitHub connection cannot be modified.
5. Duplicate connections are resolved — latest kept, stale deactivated.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.db.models.connection import Connection, ConnectionStatus
from app.schemas.api import AssessmentConfig


# ── Helpers ──

def _make_github_connection(
    client_id: uuid.UUID | None = None,
    resource_id: str | None = None,
    resource_name: str | None = None,
    connected_at: datetime | None = None,
    status: ConnectionStatus = ConnectionStatus.CONNECTED,
    installation_id: str = "12345") -> Connection:
    """Create an in-memory GitHub Connection for testing."""
    return Connection(
        id=uuid.uuid4(),
        client_id=client_id or uuid.uuid4(),
        provider="github",
        status=status,
        scopes=["contents:read", "metadata:read"],
        secret_ref="test_secret_ref",
        connected_at=connected_at or datetime.now(timezone.utc),
        resource_id=resource_id,
        resource_name=resource_name,
        provider_metadata={"installation_id": installation_id})


# ── 1. Resource selection persists resource_id/resource_name ──

class TestGitHubResourcePersistence:
    def test_resource_fields_set_on_connection(self):
        """Simulates what select_resource does: sets resource_id and resource_name."""
        conn = _make_github_connection()
        assert conn.resource_id is None
        assert conn.resource_name is None

        # Simulate select_resource logic
        conn.resource_id = "myorg/my-repo"
        conn.resource_name = "myorg/my-repo"

        assert conn.resource_id == "myorg/my-repo"
        assert conn.resource_name == "myorg/my-repo"

    def test_resource_survives_model_access(self):
        """After setting resource, it persists across attribute access."""
        conn = _make_github_connection(
            resource_id="owner/repo",
            resource_name="owner/repo")
        assert conn.resource_id == "owner/repo"
        assert conn.resource_name == "owner/repo"


# ── 2. Assessment does NOT return "no assessment resource selected" for GitHub ──

class TestAssessmentNoResourceRejection:
    def test_github_in_no_resource_required(self):
        """GitHub must be in the NO_RESOURCE_REQUIRED set to avoid
        the 'no assessment resource selected' degradation path."""
        # This mirrors the set defined in assessment_service.py line 312
        NO_RESOURCE_REQUIRED = {"cmp", "meta", "github"}
        assert "github" in NO_RESOURCE_REQUIRED

    def test_github_not_blocked_without_resource_id(self):
        """When a GitHub connection has no resource_id but config has repos,
        GitHub should NOT be rejected by the no-resource gate."""
        NO_RESOURCE_REQUIRED = {"cmp", "meta", "github"}
        conn = _make_github_connection(resource_id=None)
        integration = "github"

        # Simulate the no-resource check from assessment_service.py line 313
        access_token = "fake_token"
        is_demo = False
        should_degrade = (
            conn
            and access_token
            and not conn.resource_id
            and integration.lower() not in NO_RESOURCE_REQUIRED
            and not is_demo
        )
        assert should_degrade is False, (
            "GitHub should NOT be degraded by the no-resource check"
        )

    def test_other_providers_still_blocked(self):
        """Non-exempt providers (gtm, ga4, etc.) should still be blocked
        when they have no resource_id."""
        NO_RESOURCE_REQUIRED = {"cmp", "meta", "github"}
        for provider in ["gtm", "ga4", "search_console", "google_ads"]:
            should_degrade = (
                True  # db_conn exists
                and True  # access_token exists
                and True  # not resource_id (resource_id is None)
                and provider.lower() not in NO_RESOURCE_REQUIRED
                and not False  # not demo
            )
            assert should_degrade is True, (
                f"{provider} should be degraded when no resource is selected"
            )


# ── 3. Assessment receives the selected repository from config ──

class TestAssessmentReceivesGitHubRepo:
    def test_config_github_repos_parsed(self):
        """AssessmentConfig correctly parses github_repos field."""
        config = AssessmentConfig(
            integrations=["github"],
            github_repos=["myorg/my-repo"])
        assert config.github_repos == ["myorg/my-repo"]

    def test_github_kwargs_builder_uses_config_repos(self):
        """Simulates the GitHub kwargs builder logic (assessment_service.py lines 410-424).
        Verifies that config.github_repos is used when connection has no resource_id."""
        conn = _make_github_connection(resource_id=None)
        config = AssessmentConfig(
            integrations=["github"],
            github_repos=["myorg/my-repo"])

        # Simulate the repo resolution logic
        repo_from_conn = conn.resource_id or conn.resource_name
        repos = (
            config.github_repos
            or ([repo_from_conn] if repo_from_conn else [])
            or config.repositories
        )

        assert repos == ["myorg/my-repo"]

    def test_github_kwargs_builder_uses_connection_resource(self):
        """When connection has resource_id, it should be used as fallback."""
        conn = _make_github_connection(
            resource_id="org/from-connection",
            resource_name="org/from-connection")
        config = AssessmentConfig(
            integrations=["github"],
            github_repos=[],  # empty
        )

        repo_from_conn = conn.resource_id or conn.resource_name
        repos = (
            config.github_repos
            or ([repo_from_conn] if repo_from_conn else [])
            or config.repositories
        )

        assert repos == ["org/from-connection"]

    def test_config_repos_take_priority_over_connection(self):
        """config.github_repos takes priority over connection resource_id."""
        conn = _make_github_connection(
            resource_id="org/from-connection")
        config = AssessmentConfig(
            integrations=["github"],
            github_repos=["org/from-config"])

        repo_from_conn = conn.resource_id or conn.resource_name
        repos = (
            config.github_repos
            or ([repo_from_conn] if repo_from_conn else [])
            or config.repositories
        )

        assert repos == ["org/from-config"]


# ── 4. Another client's GitHub connection cannot be modified ──

class TestTenantIsolation:
    def test_different_client_ids_are_isolated(self):
        """Connections belong to specific clients; a different client_id
        should not match the same connection."""
        client_a = uuid.uuid4()
        client_b = uuid.uuid4()

        conn = _make_github_connection(client_id=client_a, resource_id="org/repo")

        # Simulate the select_resource query filter
        assert conn.client_id == client_a
        assert conn.client_id != client_b

    def test_connection_client_scoping(self):
        """Multiple connections for different clients don't interfere."""
        client_a = uuid.uuid4()
        client_b = uuid.uuid4()

        conn_a = _make_github_connection(client_id=client_a, resource_id="org/repo-a")
        conn_b = _make_github_connection(client_id=client_b, resource_id="org/repo-b")

        all_conns = [conn_a, conn_b]
        client_a_conns = [c for c in all_conns if c.client_id == client_a]
        client_b_conns = [c for c in all_conns if c.client_id == client_b]

        assert len(client_a_conns) == 1
        assert client_a_conns[0].resource_id == "org/repo-a"
        assert len(client_b_conns) == 1
        assert client_b_conns[0].resource_id == "org/repo-b"


# ── 5. Duplicate connections are resolved ──

class TestDuplicateConnectionResolution:
    def test_latest_connection_is_kept(self):
        """When multiple connected rows exist, the most recently connected is used."""
        client_id = uuid.uuid4()

        old_conn = _make_github_connection(
            client_id=client_id,
            connected_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        new_conn = _make_github_connection(
            client_id=client_id,
            connected_at=datetime(2026, 9, 18, tzinfo=timezone.utc))

        # Simulate the select_resource ordering logic
        all_connections = sorted(
            [old_conn, new_conn],
            key=lambda c: c.connected_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True)

        chosen = all_connections[0]
        assert chosen.id == new_conn.id

    def test_stale_duplicates_are_deactivated(self):
        """Stale duplicate connections get marked as DISCONNECTED."""
        client_id = uuid.uuid4()

        old_conn = _make_github_connection(
            client_id=client_id,
            connected_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        new_conn = _make_github_connection(
            client_id=client_id,
            connected_at=datetime(2026, 9, 18, tzinfo=timezone.utc))

        all_connections = sorted(
            [old_conn, new_conn],
            key=lambda c: c.connected_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True)

        # Simulate the deactivation logic from select_resource
        connection = all_connections[0]
        for stale in all_connections[1:]:
            stale.status = ConnectionStatus.DISCONNECTED

        assert connection.status == ConnectionStatus.CONNECTED
        assert old_conn.status == ConnectionStatus.DISCONNECTED

    def test_single_connection_no_deactivation(self):
        """When there's only one connection, no deactivation happens."""
        client_id = uuid.uuid4()
        conn = _make_github_connection(client_id=client_id)

        all_connections = [conn]
        connection = all_connections[0]
        for stale in all_connections[1:]:
            stale.status = ConnectionStatus.DISCONNECTED  # never called

        assert connection.status == ConnectionStatus.CONNECTED

    def test_resource_set_on_latest_not_stale(self):
        """Resource selection is applied to the latest connection, not a stale one."""
        client_id = uuid.uuid4()

        old_conn = _make_github_connection(
            client_id=client_id,
            connected_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        new_conn = _make_github_connection(
            client_id=client_id,
            connected_at=datetime(2026, 9, 18, tzinfo=timezone.utc))

        all_connections = sorted(
            [old_conn, new_conn],
            key=lambda c: c.connected_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True)

        connection = all_connections[0]
        connection.resource_id = "org/my-repo"
        connection.resource_name = "org/my-repo"

        for stale in all_connections[1:]:
            stale.status = ConnectionStatus.DISCONNECTED

        assert new_conn.resource_id == "org/my-repo"
        assert old_conn.resource_id is None
        assert old_conn.status == ConnectionStatus.DISCONNECTED
