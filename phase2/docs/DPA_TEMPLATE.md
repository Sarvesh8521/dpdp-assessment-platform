# Data Processing Agreement (DPA) Template
**Status:** DRAFT - Requires Legal Review

This Data Processing Agreement ("DPA") is an addendum to the Master Services Agreement between Interactive Avenues ("Processor") and the Client ("Controller").

## 1. Scope of Processing
Processor provides a DPDP Assessment Platform. The processing is strictly limited to reading configuration and metadata from Controller's connected marketing and analytics platforms (e.g., GTM, GA4, Meta, Google Ads).

## 2. Prohibition on Personal Data
Processor is explicitly prohibited from storing, analyzing, or processing end-user personal data (such as emails, phone numbers, or behavioral rows). All API scopes requested shall be Read-Only.

## 3. Processor Obligations
- **Security:** Processor shall encrypt all API tokens at rest using a managed secret store.
- **Isolation:** Processor shall enforce logical tenant isolation ensuring no cross-client data access.
- **Retention:** Processor shall delete all assessment metadata and findings within 30 days of assessment completion.
- **Audit:** Processor shall maintain an append-only audit log of every API call made on behalf of the Controller.

## 4. Sub-processors
Processor utilizes standard cloud infrastructure (e.g., AWS/GCP) but shall not engage any third-party to process the metadata without prior written notice to the Controller.

*[Legal Counsel: Insert governing law, liability caps, and standard clauses here]*
