"""Secret Manager abstraction.

Provides a unified interface for secret storage across environments:
- Local development: encrypted file-based storage (Fernet symmetric encryption)
- Production: Google Secret Manager

HARD RULES (PHASE2_SPEC Section 1, Rules 4-5):
  - Credentials never touch the database in plaintext
  - Tokens encrypted at rest, scoped per client, revocable
  - Never log tokens, never expose to frontend
"""

from __future__ import annotations

import abc
import base64
import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SecretManagerError(Exception):
    """Base exception for secret management errors."""


class SecretNotFoundError(SecretManagerError):
    """Secret not found."""


class SecretExpiredError(SecretManagerError):
    """Secret has expired."""


class SecretRevokedError(SecretManagerError):
    """Secret has been revoked."""


class BaseSecretManager(abc.ABC):
    """Abstract secret manager interface."""

    @abc.abstractmethod
    async def store_secret(
        self,
        client_id: str,
        secret_name: str,
        secret_value: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a secret, returning a reference ID.

        Args:
            client_id: The client this secret belongs to (tenant isolation).
            secret_name: Logical name for the secret.
            secret_value: The secret value to encrypt and store.
            metadata: Optional metadata about the secret (no PII).

        Returns:
            A reference ID that can be used to retrieve the secret.
        """

    @abc.abstractmethod
    async def get_secret(
        self,
        client_id: str,
        secret_ref: str,
    ) -> str:
        """Retrieve a secret by its reference.

        Args:
            client_id: Must match the client_id used during storage (isolation).
            secret_ref: The reference ID returned by store_secret.

        Returns:
            The decrypted secret value.

        Raises:
            SecretNotFoundError: If the secret doesn't exist.
            SecretRevokedError: If the secret was revoked.
        """

    @abc.abstractmethod
    async def revoke_secret(
        self,
        client_id: str,
        secret_ref: str,
    ) -> None:
        """Revoke a secret, making it inaccessible.

        Args:
            client_id: Must match the client_id used during storage.
            secret_ref: The reference ID.
        """

    @abc.abstractmethod
    async def rotate_secret(
        self,
        client_id: str,
        secret_ref: str,
        new_value: str,
    ) -> str:
        """Rotate a secret to a new value.

        Args:
            client_id: Must match the client_id used during storage.
            secret_ref: The current reference ID.
            new_value: The new secret value.

        Returns:
            New reference ID (may be same or different depending on backend).
        """

    @abc.abstractmethod
    async def list_secrets(
        self,
        client_id: str,
    ) -> list[dict[str, Any]]:
        """List all secrets for a client (metadata only, never values)."""

    @abc.abstractmethod
    async def delete_all_client_secrets(
        self,
        client_id: str,
    ) -> int:
        """Delete all secrets for a client. Returns count deleted."""


class LocalSecretManager(BaseSecretManager):
    """Local file-based encrypted secret manager for development.

    Uses Fernet (AES-128-CBC) symmetric encryption.
    Secrets are stored in a local directory, encrypted at rest.
    """

    def __init__(self, encryption_key: str | None = None, storage_dir: str | None = None):
        settings = get_settings()
        raw_key = encryption_key or settings.local_secret_encryption_key

        # Derive a valid Fernet key from the provided key
        key_bytes = raw_key.encode("utf-8")[:32].ljust(32, b"\0")
        self._fernet = Fernet(base64.urlsafe_b64encode(key_bytes))

        self._storage_dir = Path(storage_dir or ".secrets")
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        logger.info("local_secret_manager_initialized", storage_dir=str(self._storage_dir))

    def _client_dir(self, client_id: str) -> Path:
        """Get the storage directory for a specific client."""
        safe_id = client_id.replace("/", "_").replace("\\", "_")
        client_dir = self._storage_dir / safe_id
        client_dir.mkdir(parents=True, exist_ok=True)
        return client_dir

    def _secret_path(self, client_id: str, secret_ref: str) -> Path:
        return self._client_dir(client_id) / f"{secret_ref}.enc"

    def _meta_path(self, client_id: str, secret_ref: str) -> Path:
        return self._client_dir(client_id) / f"{secret_ref}.meta"

    async def store_secret(
        self,
        client_id: str,
        secret_name: str,
        secret_value: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        import uuid
        secret_ref = str(uuid.uuid4())

        # Encrypt the secret value
        encrypted = self._fernet.encrypt(secret_value.encode("utf-8"))
        self._secret_path(client_id, secret_ref).write_bytes(encrypted)

        # Store metadata (never the secret itself)
        meta = {
            "client_id": client_id,
            "secret_name": secret_name,
            "secret_ref": secret_ref,
            "revoked": False,
            **(metadata or {}),
        }
        self._meta_path(client_id, secret_ref).write_text(
            json.dumps(meta), encoding="utf-8"
        )

        logger.info("secret_stored", client_id=client_id, secret_name=secret_name)
        return secret_ref

    async def get_secret(
        self,
        client_id: str,
        secret_ref: str,
    ) -> str:
        secret_path = self._secret_path(client_id, secret_ref)
        meta_path = self._meta_path(client_id, secret_ref)

        if not secret_path.exists():
            raise SecretNotFoundError(f"Secret {secret_ref} not found")

        # Check revocation
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("revoked"):
                raise SecretRevokedError(f"Secret {secret_ref} has been revoked")
            # Verify tenant isolation
            if meta.get("client_id") != client_id:
                raise SecretNotFoundError(f"Secret {secret_ref} not found")

        try:
            encrypted = secret_path.read_bytes()
            return self._fernet.decrypt(encrypted).decode("utf-8")
        except InvalidToken as exc:
            raise SecretManagerError(f"Failed to decrypt secret {secret_ref}") from exc

    async def revoke_secret(
        self,
        client_id: str,
        secret_ref: str,
    ) -> None:
        meta_path = self._meta_path(client_id, secret_ref)
        if not meta_path.exists():
            raise SecretNotFoundError(f"Secret {secret_ref} not found")

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("client_id") != client_id:
            raise SecretNotFoundError(f"Secret {secret_ref} not found")

        meta["revoked"] = True
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        # Remove the encrypted secret file
        secret_path = self._secret_path(client_id, secret_ref)
        if secret_path.exists():
            secret_path.unlink()

        logger.info("secret_revoked", client_id=client_id, secret_ref=secret_ref)

    async def rotate_secret(
        self,
        client_id: str,
        secret_ref: str,
        new_value: str,
    ) -> str:
        # Verify the old secret exists and is accessible
        await self.get_secret(client_id, secret_ref)

        # Get the metadata
        meta_path = self._meta_path(client_id, secret_ref)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        # Store the new secret
        new_ref = await self.store_secret(
            client_id,
            meta.get("secret_name", "rotated"),
            new_value,
            metadata=meta.get("metadata"),
        )

        # Revoke the old one
        await self.revoke_secret(client_id, secret_ref)

        logger.info("secret_rotated", client_id=client_id, old_ref=secret_ref, new_ref=new_ref)
        return new_ref

    async def list_secrets(
        self,
        client_id: str,
    ) -> list[dict[str, Any]]:
        client_dir = self._client_dir(client_id)
        secrets = []
        for meta_file in client_dir.glob("*.meta"):
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("client_id") == client_id:
                # Never return the actual secret value
                secrets.append({
                    "secret_ref": meta["secret_ref"],
                    "secret_name": meta.get("secret_name"),
                    "revoked": meta.get("revoked", False),
                })
        return secrets

    async def delete_all_client_secrets(
        self,
        client_id: str,
    ) -> int:
        client_dir = self._client_dir(client_id)
        count = 0
        for f in client_dir.iterdir():
            f.unlink()
            count += 1
        if client_dir.exists():
            client_dir.rmdir()
        logger.info("client_secrets_deleted", client_id=client_id, count=count)
        return count


class GCPSecretManager(BaseSecretManager):
    """Google Cloud Secret Manager backend for production.

    Uses Google Secret Manager API for secure, managed secret storage.
    """

    def __init__(self) -> None:
        from google.cloud import secretmanager

        settings = get_settings()
        self._client = secretmanager.SecretManagerServiceClient()
        self._project_id = settings.gcp_project_id
        self._prefix = settings.gcp_secret_prefix

        if not self._project_id:
            raise SecretManagerError("GCP project ID is required for GCP secret manager")

        logger.info("gcp_secret_manager_initialized", project_id=self._project_id)

    def _secret_id(self, client_id: str, secret_ref: str) -> str:
        safe_client = client_id.replace("-", "_")
        safe_ref = secret_ref.replace("-", "_")
        return f"{self._prefix}_{safe_client}_{safe_ref}"

    def _parent(self) -> str:
        return f"projects/{self._project_id}"

    async def store_secret(
        self,
        client_id: str,
        secret_name: str,
        secret_value: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        import uuid
        secret_ref = str(uuid.uuid4())
        secret_id = self._secret_id(client_id, secret_ref)

        # Create the secret
        self._client.create_secret(
            request={
                "parent": self._parent(),
                "secret_id": secret_id,
                "secret": {
                    "replication": {"automatic": {}},
                    "labels": {
                        "client_id": client_id.replace("-", "_")[:63],
                        "secret_name": secret_name[:63],
                        "managed_by": "dpdp_phase2",
                    },
                },
            }
        )

        # Add the secret version
        self._client.add_secret_version(
            request={
                "parent": f"{self._parent()}/secrets/{secret_id}",
                "payload": {"data": secret_value.encode("utf-8")},
            }
        )

        logger.info("secret_stored_gcp", client_id=client_id, secret_name=secret_name)
        return secret_ref

    async def get_secret(
        self,
        client_id: str,
        secret_ref: str,
    ) -> str:
        secret_id = self._secret_id(client_id, secret_ref)
        try:
            response = self._client.access_secret_version(
                request={
                    "name": f"{self._parent()}/secrets/{secret_id}/versions/latest",
                }
            )
            return response.payload.data.decode("utf-8")
        except Exception as exc:
            if "NOT_FOUND" in str(exc):
                raise SecretNotFoundError(f"Secret {secret_ref} not found") from exc
            raise SecretManagerError(f"Failed to access secret: {exc}") from exc

    async def revoke_secret(
        self,
        client_id: str,
        secret_ref: str,
    ) -> None:
        secret_id = self._secret_id(client_id, secret_ref)
        try:
            # Destroy all versions
            secret_name = f"{self._parent()}/secrets/{secret_id}"
            versions = self._client.list_secret_versions(
                request={"parent": secret_name}
            )
            for version in versions:
                if version.state.name == "ENABLED":
                    self._client.destroy_secret_version(
                        request={"name": version.name}
                    )
            logger.info("secret_revoked_gcp", client_id=client_id, secret_ref=secret_ref)
        except Exception as exc:
            raise SecretManagerError(f"Failed to revoke secret: {exc}") from exc

    async def rotate_secret(
        self,
        client_id: str,
        secret_ref: str,
        new_value: str,
    ) -> str:
        secret_id = self._secret_id(client_id, secret_ref)
        try:
            self._client.add_secret_version(
                request={
                    "parent": f"{self._parent()}/secrets/{secret_id}",
                    "payload": {"data": new_value.encode("utf-8")},
                }
            )
            logger.info("secret_rotated_gcp", client_id=client_id, secret_ref=secret_ref)
            return secret_ref
        except Exception as exc:
            raise SecretManagerError(f"Failed to rotate secret: {exc}") from exc

    async def list_secrets(
        self,
        client_id: str,
    ) -> list[dict[str, Any]]:
        safe_client = client_id.replace("-", "_")
        filter_str = f"labels.client_id={safe_client} AND labels.managed_by=dpdp_phase2"
        try:
            secrets = self._client.list_secrets(
                request={
                    "parent": self._parent(),
                    "filter": filter_str,
                }
            )
            return [
                {
                    "secret_ref": s.name.split("_")[-1] if "_" in s.name else s.name,
                    "secret_name": s.labels.get("secret_name", ""),
                    "revoked": False,
                }
                for s in secrets
            ]
        except Exception as exc:
            raise SecretManagerError(f"Failed to list secrets: {exc}") from exc

    async def delete_all_client_secrets(
        self,
        client_id: str,
    ) -> int:
        secrets = await self.list_secrets(client_id)
        for secret in secrets:
            await self.revoke_secret(client_id, secret["secret_ref"])
        return len(secrets)


def get_secret_manager() -> BaseSecretManager:
    """Factory function to get the appropriate secret manager."""
    settings = get_settings()
    if settings.secret_backend == "gcp":
        return GCPSecretManager()
    return LocalSecretManager()
