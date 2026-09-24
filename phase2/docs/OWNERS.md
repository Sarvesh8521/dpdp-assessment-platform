# Platform Ownership

The DPDP Assessment Platform operates as a Data Processor on behalf of our clients. To ensure accountability, the following roles and owners are designated.

## Accountable Owner
**Role:** DPDP Platform Product Manager / Data Protection Officer equivalent
**Responsibilities:** 
- Final authority on all data retention and deletion processes.
- Ensuring no personal data is stored by the application.
- Managing legal and DPA template reviews.

## Engineering Owner
**Role:** Lead Engineer
**Responsibilities:**
- Maintenance of the `AuditedHTTPClient` and enforcement of read-only scopes.
- Security of the token and secret management systems.
- Execution of the automated retention cleanup jobs.
