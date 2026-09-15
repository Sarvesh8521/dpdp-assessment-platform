# Architecture Decision Record: Phase 1 vs Phase 2 Divergence

## Context

Phase 1 (`dpdp-scanner-v1`) was built to demonstrate immediate value through active scanning. It heavily utilized dynamic tools such as Playwright and local proxy-based interceptors to gather on-the-fly network events and DOM observations from public-facing URLs. While this approach provided rich, real-time discovery of trackers, it came with limitations regarding authentication, tenant isolation, and risk of accidental data modification on client properties.

Phase 2 (`dpdp-scanner-v2`) pivots strictly to a **Production-Grade, Read-Only, Integration-Driven** architecture.

## Decision

We deliberately diverge from the Phase 1 dynamic scanning approach in favor of static, API-driven integrations (e.g., GitHub, Google Tag Manager, Google Analytics 4).

### Key Architectural Shifts

1.  **Read-Only API Adapters Instead of Active Crawling:**
    *   Instead of visiting a URL and observing network requests (Phase 1), Phase 2 connects to provider APIs (like GitHub, GTM) to read configurations statically.
    *   **Why:** Ensures zero risk of side-effects on client production systems. Active scanning can inadvertently trigger analytics events or affect metrics. Read-only API access guarantees the scanner acts as an auditor, not an actor.

2.  **Strict Security Controls (SSRF & Forbidden Files):**
    *   Phase 2 implements an `AuditedHTTPClient` that explicitly blocks SSRF (Server-Side Request Forgery) attacks by prohibiting internal IP routing (e.g., `127.0.0.1`, `169.254.169.254`).
    *   Specific API endpoints that involve destructive or sensitive actions (e.g., `/repos/*/contents` with write methods, individual consent records) are hard-blocked.
    *   File reads via integrations (like GitHub) explicitly exclude sensitive secrets (e.g., `.env`, `credentials.json`).

3.  **PII Sanitization at the Edge:**
    *   Instead of processing raw data deeply into the system, all API responses undergo immediate PII sanitization. Keys matching `PII_FIELD_NAMES` are masked with robust SHA-256 hashes (`[REDACTED: hash]`).
    *   This ensures that no PII accidentally leaks into our database or logs.

4.  **Multi-Tenant Data Isolation:**
    *   Phase 2 enforces strict Row-Level Security (RLS) equivalents via the `client_id`. Every assessment, connection, and finding is strongly bound to a specific tenant.
    *   The backend validates `auth.require_client_access(client_id)` before any operation, ensuring users cannot access or trigger scans for other tenants.

## Consequences

*   **Positive:** The platform is significantly more secure, auditable, and compliant with privacy regulations by design. We can confidently deploy this to enterprise clients without risking their live data or infrastructure.
*   **Positive:** Evidence generation is deterministic (based on API configs) rather than probabilistic (based on crawler timing or ad-blocker interference).
*   **Negative/Trade-off:** We lose the ability to detect undocumented, "rogue" trackers that are injected dynamically outside of known tag managers or SDK manifests. This is mitigated by combining static repo analysis with configuration analysis (which catches the vast majority of intended data sharing).

## Conclusion

The shift from Phase 1 to Phase 2 represents the transition from a "Proof of Concept Discovery Tool" to a "Production Auditing Platform." The strict read-only, statically analyzed, and heavily sandboxed approach is mandatory for the privacy and security guarantees required by a DPDP compliance platform.
