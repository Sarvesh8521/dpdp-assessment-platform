"""GitHub adapter — build last, scope narrowly. PHASE2_SPEC Section 4.6.

GitHub App (NOT personal access token), scoped to named repositories.
Static analysis only. Ephemeral container/tmpfs.

NEVER: clone to persistent storage, store source code, read .env/secrets, write operations.
Evidence store gets findings + file references, never source code.
"""

from __future__ import annotations

import os
import re
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.domain.evidence import Confidence, EvidenceItem, Verdict
from app.domain.obligations import get_obligations_for_integration
from app.integrations.base.adapter import (
    AdapterResult, ConnectionInfo, HealthStatus, IntegrationAdapter,
)
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
from app.domain.evidence import StructuredEvidence

logger = get_logger(__name__)

GITHUB_FORBIDDEN_ENDPOINTS = [
    # Secrets / credentials
    "/repos/*/actions/secrets",
    "/repos/*/environments/*/secrets",
    "/repos/*/dependabot/secrets",
    "actions/secrets",
    # Write operations
    "/repos/*/contents",  # PUT/POST on contents = write
    "/repos/*/git/refs",
    "/repos/*/git/commits",
    "/repos/*/merges",
    "/repos/*/pulls",  # Creating PRs
    "/repos/*/issues",  # Creating issues
    "/repos/*/dispatches",
    "/repos/*/hooks",
]

# Files that must NEVER be read
FORBIDDEN_FILES = {
    ".env", ".env.local", ".env.production", ".env.staging",
    ".env.development", ".env.test",
    "credentials.json", "service-account.json",
    "id_rsa", "id_ed25519", ".npmrc",  # May contain tokens
}

# Tracker SDKs to detect
TRACKER_SDKS = {
    "firebase": ["com.google.firebase", "firebase-analytics", "@react-native-firebase"],
    "branch": ["io.branch.sdk", "branch-sdk", "react-native-branch"],
    "appsflyer": ["com.appsflyer", "react-native-appsflyer", "appsflyer-flutter"],
    "clevertap": ["com.clevertap", "clevertap-react-native"],
    "moengage": ["com.moengage", "react-native-moengage"],
    "meta": ["com.facebook.sdk", "react-native-fbsdk", "facebook-android-sdk"],
    "google_analytics": ["com.google.android.gms:play-services-analytics"],
    "google_ads": ["com.google.android.gms:play-services-ads"],
}

# Sensitive Android permissions
SENSITIVE_PERMISSIONS = {
    "android.permission.READ_CONTACTS",
    "android.permission.READ_PHONE_STATE",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.CAMERA",
    "android.permission.RECORD_AUDIO",
    "android.permission.READ_CALL_LOG",
    "android.permission.READ_SMS",
    "android.permission.READ_CALENDAR",
    "android.permission.BODY_SENSORS",
}

# Sensitive iOS permissions (Info.plist keys)
IOS_SENSITIVE_PERMISSIONS = {
    "NSLocationWhenInUseUsageDescription",
    "NSLocationAlwaysUsageDescription",
    "NSLocationAlwaysAndWhenInUseUsageDescription",
    "NSCameraUsageDescription",
    "NSMicrophoneUsageDescription",
    "NSContactsUsageDescription",
    "NSCalendarsUsageDescription",
    "NSPhotoLibraryUsageDescription",
}

# Tracker SDK initialization patterns
TRACKER_SDK_INIT_PATTERNS = [
    re.compile(r"FirebaseApp\.initializeApp", re.IGNORECASE),
    re.compile(r"branch\.initSession", re.IGNORECASE),
    re.compile(r"appsFlyer\.initSdk", re.IGNORECASE),
    re.compile(r"CleverTapAPI\.createInstance", re.IGNORECASE),
    re.compile(r"MoEngage\.Builder", re.IGNORECASE),
    re.compile(r"AppEventsLogger\.activateApp", re.IGNORECASE),
    re.compile(r"GoogleAnalytics\.getInstance", re.IGNORECASE),
]

# Consent gating patterns
CONSENT_GATING_PATTERNS = [
    re.compile(r"if\s*\([^)]*(?:consent|granted|allow)[^)]*\)", re.IGNORECASE),
]

# Personal data form fields patterns
FORM_FIELD_PATTERNS = [
    re.compile(r'<input[^>]*type=[\'"](?:email|password|tel)[\'"]', re.IGNORECASE),
    re.compile(r'secureTextEntry', re.IGNORECASE),
    re.compile(r'<TextInput[^>]*textContentType=[\'"](?:emailAddress|password|telephoneNumber)[\'"]', re.IGNORECASE),
    re.compile(r'android:inputType=[\'"]text(?:EmailAddress|Password|Phonetic)[\'"]', re.IGNORECASE),
]

# PII logging patterns
PII_LOG_PATTERNS = [
    re.compile(r"Log\.\w+\(.*(?<![a-z])(?:email|phone|name|address|ssn|aadhaar)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"console\.log\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"print\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"logger\..*\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
    re.compile(r"NSLog\(.*(?<![a-z])(?:email|phone|name|address)(?![a-z]).*\)", re.IGNORECASE),
]

class GitHubAdapter(IntegrationAdapter):
    @property
    def id(self) -> str:
        return "github"

    @property
    def provider(self) -> str:
        return "github"

    @property
    def required_scopes(self) -> list[str]:
        return ["contents:read", "metadata:read"]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("github")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            "contents:read": [
                "S6.consentGating", "APP.sdk.consent",
                "APP.permissions.justified", "APP.logging.noPII",
                "S10.scope.sdks", "S10.scope.platforms",
                "APP.forms.disclosure",
            ],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return GITHUB_FORBIDDEN_ENDPOINTS

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider, scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
            metadata={"installation_id": credentials.get("installation_id", "")},
        )

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        """Check the health of a GitHub App connection.

        Makes a real API call to verify the installation token works.
        Does NOT blindly return healthy=True.
        """
        installation_id = connection.metadata.get("installation_id", "")

        if not installation_id:
            return HealthStatus(
                healthy=False, provider=self.provider,
                message="No installation_id — GitHub App may not be installed",
                checked_at=datetime.now(timezone.utc),
            )

        try:
            # Try to list repos with the provided access token
            # (should be an installation token)
            client = AuditedHTTPClient(
                provider=self.provider,
                base_url="https://api.github.com",
                access_token=connection.metadata.get("access_token", ""),
                forbidden_endpoints=self.forbidden_endpoints,
                obligations_served=self.obligations_answered,
            )
            try:
                resp = await client.get(
                    "/installation/repositories",
                    params={"per_page": 1},
                    operation="health_check_repos",
                )
                total = resp.get("total_count", 0)
                return HealthStatus(
                    healthy=True, provider=self.provider,
                    message=f"GitHub App installed, {total} repo(s) accessible",
                    checked_at=datetime.now(timezone.utc),
                    details={"total_repos": total, "installation_id": installation_id},
                )
            except ExternalAPIError as exc:
                return HealthStatus(
                    healthy=False, provider=self.provider,
                    message=f"GitHub API error: {exc.status_code}",
                    checked_at=datetime.now(timezone.utc),
                    details={"error": str(exc)},
                )
            finally:
                await client.close()
        except Exception as e:
            return HealthStatus(
                healthy=False, provider=self.provider,
                message=f"Health check failed: {str(e)}",
                checked_at=datetime.now(timezone.utc),
            )

    async def collect(
        self, connection: ConnectionInfo, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", audit_callback: Any = None,
        repositories: list[str] | None = None,
    ) -> AdapterResult:
        """Static analysis of repositories. Ephemeral — no code persisted."""
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        warnings: list[str] = []
        now = datetime.now(timezone.utc)

        if not repositories:
            evidence.append(EvidenceItem(
                obligation_id="APP.sdk.consent",
                verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                source="github:no_repos", observed_at=now,
                detail="No repositories configured for analysis.",
            ))
            return AdapterResult(evidence=evidence)

        client = AuditedHTTPClient(
            provider=self.provider, base_url="https://api.github.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            for repo in repositories:
                try:
                    await self._analyze_repository(
                        client, repo, evidence, errors, warnings, now,
                    )
                except ExternalAPIError as exc:
                    errors.append(f"Failed to analyze {repo}: {exc}")
                    for obl in self.obligations_answered:
                        evidence.append(EvidenceItem(
                            obligation_id=obl,
                            verdict=Verdict.CANNOT_DETERMINE,
                            confidence=Confidence.LOW,
                            source=f"github:{repo}:error",
                            observed_at=now,
                            detail=f"Failed to access repository {repo}: {exc}"
                        ))

            return AdapterResult(evidence=evidence, errors=errors, warnings=warnings)
        finally:
            await client.close()

    async def _analyze_repository(
        self,
        client: AuditedHTTPClient,
        repo: str,
        evidence: list[EvidenceItem],
        errors: list[str],
        warnings: list[str],
        now: datetime,
    ) -> None:
        """Analyze a single repository — static analysis, no persistent storage."""
        sdk_files_map = {
            "package.json": "npm",
            "Podfile": "cocoapods",
            "build.gradle": "gradle",
            "pubspec.yaml": "flutter",
        }
        
        ALLOWED_EXTENSIONS = {
            ".java", ".kt", ".swift", ".js", ".ts", ".jsx", ".tsx",
            ".dart", ".html", ".xml", ".plist", ".json", ".yaml", ".gradle"
        }
        
        FORBIDDEN_DIRS = {
            ".git", "node_modules", "build", "dist", "generated", "out"
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / "repo.tar.gz"
            try:
                await client.download_file(
                    f"/repos/{repo}/tarball",
                    str(tar_path),
                    operation="download_repo_archive",
                )
            except ExternalAPIError as exc:
                raise exc
            
            # Extract tarball
            try:
                with tarfile.open(tar_path, "r:gz") as tar:
                    def is_within_directory(directory: str, target: str) -> bool:
                        abs_directory = os.path.abspath(directory)
                        abs_target = os.path.abspath(target)
                        prefix = os.path.commonprefix([abs_directory, abs_target])
                        return prefix == abs_directory

                    def safe_extract(tar: tarfile.TarFile, path: str = ".") -> None:
                        for member in tar.getmembers():
                            member_path = os.path.join(path, member.name)
                            if not is_within_directory(path, member_path):
                                raise Exception("Attempted Path Traversal in Tar File")
                        tar.extractall(path)

                    safe_extract(tar, tmpdir)
            except Exception as exc:
                errors.append(f"Failed to extract archive for {repo}: {exc}")
                return

            extracted_items = list(Path(tmpdir).iterdir())
            if tar_path in extracted_items:
                extracted_items.remove(tar_path)
                
            if not extracted_items:
                warnings.append(f"Repository {repo} appears to be empty.")
                return
                
            extracted_repo_root = extracted_items[0] if extracted_items[0].is_dir() else Path(tmpdir)

            for root, dirs, files in os.walk(extracted_repo_root):
                # Filter dirs in-place
                dirs[:] = [d for d in dirs if d not in FORBIDDEN_DIRS and not d.startswith(".")]
                
                for file_name in files:
                    if file_name in FORBIDDEN_FILES:
                        continue
                        
                    file_path = Path(root) / file_name
                    rel_path = str(file_path.relative_to(extracted_repo_root)).replace("\\", "/")
                    
                    is_sdk_file = file_name in sdk_files_map
                    is_allowed_ext = file_path.suffix in ALLOWED_EXTENSIONS or file_name in {"AndroidManifest.xml", "Info.plist"}
                    
                    if not (is_sdk_file or is_allowed_ext):
                        continue
                        
                    # Skip large files (e.g. > 1MB)
                    try:
                        if file_path.stat().st_size > 1024 * 1024:
                            continue
                    except OSError:
                        continue
                        
                    try:
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                        
                    # 1. SDK inventory
                    if is_sdk_file:
                        pkg_manager = sdk_files_map.get(file_name, "unknown")
                        for sdk_name, patterns in TRACKER_SDKS.items():
                            for pattern in patterns:
                                if pattern.lower() in content.lower():
                                    line_no = 1
                                    for i, line in enumerate(content.splitlines(), 1):
                                        if pattern.lower() in line.lower():
                                            line_no = i
                                            break
                                            
                                    evidence.append(EvidenceItem(
                                        obligation_id="S10.scope.sdks",
                                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                        source=f"github:{repo}:{rel_path}", observed_at=now,
                                        detail=f"Found tracker SDK '{sdk_name}' in {rel_path} at line {line_no}.",
                                        structured_detail=StructuredEvidence(
                                            source="github", integration="github", operation="static_analysis",
                                            resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{line_no}",
                                            observed_at=now, observed_state=f"Contains {sdk_name}", expected_state="SDK documented",
                                            detail=f"Found tracker SDK '{sdk_name}' in {rel_path} at line {line_no}.",
                                            reason=f"The SDK '{sdk_name}' was detected in the project manifests.",
                                            metadata={
                                                "repository": repo,
                                                "file_path": rel_path,
                                                "line": line_no,
                                                "category": "Scope",
                                                "matched_rule": "S10.scope.sdks",
                                                "recommended_action": "Document SDK in privacy policy and scope.",
                                                "manager": pkg_manager
                                            }
                                        )
                                    ))
                                    break # Only report once per SDK per file
                                    
                    # 2. Android permissions
                    if file_name == "AndroidManifest.xml":
                        for perm in SENSITIVE_PERMISSIONS:
                            if perm in content:
                                line_no = next((i for i, line in enumerate(content.splitlines(), 1) if perm in line), 1)
                                evidence.append(EvidenceItem(
                                    obligation_id="APP.permissions.justified",
                                    verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                    source=f"github:{repo}:{rel_path}:{perm}", observed_at=now,
                                    detail=f"Sensitive Android permission found: {perm} at line {line_no}.",
                                    structured_detail=StructuredEvidence(
                                        source="github", integration="github", operation="static_analysis",
                                        resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{line_no}",
                                        observed_at=now, observed_state=f"Contains {perm}", expected_state="Permission justified",
                                        detail=f"Sensitive Android permission found: {perm} at line {line_no}.",
                                        reason="A sensitive device permission was requested in the AndroidManifest.xml.",
                                        metadata={
                                            "repository": repo,
                                            "file_path": rel_path,
                                            "line": line_no,
                                            "category": "App Permissions",
                                            "matched_rule": "APP.permissions.justified",
                                            "recommended_action": "Provide justification for requested permission."
                                        }
                                    )
                                ))
                                
                    # 3. iOS permissions
                    if file_name == "Info.plist":
                        for perm in IOS_SENSITIVE_PERMISSIONS:
                            if perm in content:
                                line_no = next((i for i, line in enumerate(content.splitlines(), 1) if perm in line), 1)
                                evidence.append(EvidenceItem(
                                    obligation_id="APP.permissions.justified",
                                    verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                    source=f"github:{repo}:{rel_path}:{perm}", observed_at=now,
                                    detail=f"Sensitive iOS permission found: {perm} at line {line_no}.",
                                    structured_detail=StructuredEvidence(
                                        source="github", integration="github", operation="static_analysis",
                                        resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{line_no}",
                                        observed_at=now, observed_state=f"Contains {perm}", expected_state="Permission justified",
                                        detail=f"Sensitive iOS permission found: {perm} at line {line_no}.",
                                        reason="A sensitive device permission was requested in the Info.plist.",
                                        metadata={
                                            "repository": repo,
                                            "file_path": rel_path,
                                            "line": line_no,
                                            "category": "App Permissions",
                                            "matched_rule": "APP.permissions.justified",
                                            "recommended_action": "Provide justification for requested permission."
                                        }
                                    )
                                ))

                    # 4. Source code checks (PII, forms, SDK init)
                    if file_path.suffix in {".java", ".kt", ".swift", ".js", ".ts", ".jsx", ".tsx", ".dart", ".html"}:
                        lines = content.splitlines()
                        pii_found = False
                        form_found = False
                        init_found = False
                        
                        for i, line in enumerate(lines, 1):
                            if not pii_found:
                                for pattern in PII_LOG_PATTERNS:
                                    if pattern.search(line):
                                        pii_found = True
                                        evidence.append(EvidenceItem(
                                            obligation_id="APP.logging.noPII",
                                            verdict=Verdict.NOT_OBSERVED, confidence=Confidence.MEDIUM,
                                            source=f"github:{repo}:{rel_path}", observed_at=now,
                                            detail=f"PII detected in logging statement in {rel_path} at line {i}.",
                                            structured_detail=StructuredEvidence(
                                                source="github", integration="github", operation="static_analysis",
                                                resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{i}",
                                                observed_at=now, observed_state="PII logging present", expected_state="No PII logging",
                                                detail=f"PII detected in logging statement in {rel_path} at line {i}.",
                                                reason="Logging statement references a PII field name (email, phone, name, address).",
                                                metadata={
                                                    "repository": repo,
                                                    "file_path": rel_path,
                                                    "line": i,
                                                    "category": "Security",
                                                    "matched_rule": "APP.logging.noPII",
                                                    "recommended_action": "Remove PII from logging output."
                                                }
                                            )
                                        ))
                                        break
                                        
                            if not form_found:
                                for pattern in FORM_FIELD_PATTERNS:
                                    if pattern.search(line):
                                        form_found = True
                                        evidence.append(EvidenceItem(
                                            obligation_id="APP.forms.disclosure",
                                            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                            source=f"github:{repo}:{rel_path}", observed_at=now,
                                            detail=f"Personal data form fields detected in {rel_path} at line {i}.",
                                            structured_detail=StructuredEvidence(
                                                source="github", integration="github", operation="static_analysis",
                                                resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{i}",
                                                observed_at=now, observed_state="Form fields present", expected_state="N/A",
                                                detail=f"Personal data form fields detected in {rel_path} at line {i}.",
                                                reason="Form elements designed to collect personal data were found in the UI code.",
                                                metadata={
                                                    "repository": repo,
                                                    "file_path": rel_path,
                                                    "line": i,
                                                    "category": "Forms",
                                                    "matched_rule": "APP.forms.disclosure",
                                                    "recommended_action": "Add a privacy disclosure statement near the form."
                                                }
                                            )
                                        ))
                                        break
                                        
                            if not init_found:
                                for pattern in TRACKER_SDK_INIT_PATTERNS:
                                    match = pattern.search(line)
                                    if match:
                                        init_found = True
                                        is_gated = any(p.search(content) for p in CONSENT_GATING_PATTERNS)
                                        
                                        evidence.append(EvidenceItem(
                                            obligation_id="S10.scope.sdks",
                                            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                            source=f"github:{repo}:{rel_path}", observed_at=now,
                                            detail=f"Tracker SDK initialization detected in {rel_path} at line {i}.",
                                            structured_detail=StructuredEvidence(
                                                source="github", integration="github", operation="static_analysis",
                                                resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{i}",
                                                observed_at=now, observed_state="SDK init present", expected_state="N/A",
                                                detail=f"Tracker SDK initialization detected in {rel_path} at line {i}.",
                                                reason="A tracker SDK initialization was observed in the source code.",
                                                metadata={
                                                    "repository": repo,
                                                    "file_path": rel_path,
                                                    "line": i,
                                                    "category": "Scope",
                                                    "matched_rule": "S10.scope.sdks",
                                                    "recommended_action": "Verify SDK initialization is intended.",
                                                    "sdk_init": match.group(0)
                                                }
                                            )
                                        ))
                                        
                                        if not is_gated:
                                            evidence.append(EvidenceItem(
                                                obligation_id="S6.consentGating",
                                                verdict=Verdict.NOT_OBSERVED, confidence=Confidence.MEDIUM,
                                                source=f"github:{repo}:{rel_path}:consent_gating", observed_at=now,
                                                detail=f"SDK initialization in {rel_path} at line {i} has no detectable consent gating.",
                                                structured_detail=StructuredEvidence(
                                                    source="github", integration="github", operation="static_analysis",
                                                    resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{i}",
                                                    observed_at=now, observed_state="No consent gating", expected_state="Consent gating present",
                                                    detail=f"SDK initialization in {rel_path} at line {i} has no detectable consent gating.",
                                                    reason="SDK is initialized without any detectable consent check guard.",
                                                    metadata={
                                                        "repository": repo,
                                                        "file_path": rel_path,
                                                        "line": i,
                                                        "category": "Consent",
                                                        "matched_rule": "S6.consentGating",
                                                        "recommended_action": "Gate initialization behind a consent check."
                                                    }
                                                )
                                            ))
                                        else:
                                            evidence.append(EvidenceItem(
                                                obligation_id="S6.consentGating",
                                                verdict=Verdict.OBSERVED, confidence=Confidence.MEDIUM,
                                                source=f"github:{repo}:{rel_path}:consent_gating", observed_at=now,
                                                detail=f"SDK initialization in {rel_path} at line {i} has detectable consent gating.",
                                                structured_detail=StructuredEvidence(
                                                    source="github", integration="github", operation="static_analysis",
                                                    resource_type="file", resource_id=f"{repo}/{rel_path}", resource_name=file_name, location=f"{repo} > {rel_path}#L{i}",
                                                    observed_at=now, observed_state="Consent gating present", expected_state="Consent gating present",
                                                    detail=f"SDK initialization in {rel_path} at line {i} has detectable consent gating.",
                                                    reason="SDK initialization is guarded by a consent check.",
                                                    metadata={
                                                        "repository": repo,
                                                        "file_path": rel_path,
                                                        "line": i,
                                                        "category": "Consent",
                                                        "matched_rule": "S6.consentGating",
                                                        "recommended_action": "None."
                                                    }
                                                )
                                            ))
                                        break
