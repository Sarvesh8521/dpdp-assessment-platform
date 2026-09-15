# DPDP Assessment Platform — Phase 2 Build Specification

**For:** the developer building this with Claude Code / Cursor
**Status:** specification, not yet approved for build
**Prerequisite:** Section 1 must be resolved before a single line of code is written

---

## 0. What changes in Phase 2

Phase 1 observes a public website with no credentials. It reaches roughly 45% of the
obligation set.

Phase 2 connects to the client's own systems with read-only access and reaches
roughly 75%. The remaining 25% is contracts, internal policy and legal judgement,
and will never automate.

**The output does not change in kind.** Phase 2 produces the same three verdicts —
Observed, Not observed, Cannot determine — against the same obligation register.
It just reaches more of it.

---

## 1. STOP — read this before building

Phase 1 loads public pages. Phase 2 reads the client's data through authenticated
APIs. That makes Interactive Avenues a **Data Processor** under the DPDP Act, acting
on behalf of the client as Data Fiduciary.

A compliance tool that is itself non-compliant is an unacceptable outcome, and it is
the first thing a competent client-side legal team will probe.

**These must exist before build starts:**

| Requirement | Why |
|---|---|
| Data Processing Agreement template, reviewed by counsel | Processing on the client's behalf must be under contract |
| Named internal owner for the platform | Accountability cannot be diffuse |
| Documented retention period for everything the tool stores | We are subject to the same rules we assess |
| Deletion procedure, tested | Same |

**Hard rules for the code. These are not negotiable and must be enforced in code, not by convention:**

1. **Never store personal data.** The tool reads *configuration and metadata*, never
   customer records. No email addresses, phone numbers, names, IDs or event payloads
   containing them. If an API returns personal data, discard it in the adapter layer
   before it reaches storage.
2. **Read-only scopes only.** Every OAuth scope requested must be read-only. If a
   read-only scope does not exist for a needed call, do not make the call.
3. **Least privilege.** Request the narrowest scope that answers the obligation.
   Document, per scope, which obligation requires it.
4. **Audit log everything.** Every API call: who, what, when, which client, which
   obligation it served. Append-only. Retained per policy.
5. **Credentials never touch the database in plaintext.** Use a managed secret store.
   Tokens encrypted at rest, scoped per client, revocable in one action.
6. **Per-client isolation.** No query can ever return data across clients. Enforce at
   the data layer, not in application logic.
7. **Time-boxed access.** Tokens expire at the end of the engagement by default.

If any of the above is inconvenient, the answer is to reduce scope, not to relax the rule.

---

## 2. What we are not building

Do not build any of this. Consent platforms do it, they do it well, and competing
is a poor use of the budget:

- Consent banner serving
- Consent record storage as a system of record
- Preference centres
- Automatic tag blocking
- Rights-request workflow management
- Cookie auto-classification as a product

We assess, we report, and we tell the client what to configure in the platform they
already own. That boundary is also the commercial position — keep it visible in the
product.

---

## 3. Architecture

```
                        ┌──────────────────────────┐
                        │   Assessment Orchestrator │
                        │   (obligation register)   │
                        └────────────┬──────────────┘
                                     │
        ┌────────────────────────────┼────────────────────────────┐
        │                            │                            │
   ┌────▼─────┐              ┌───────▼───────┐            ┌───────▼──────┐
   │ Phase 1  │              │  Integration  │            │  Evidence    │
   │ Scanner  │              │   Adapters    │            │  Store       │
   │ (public) │              │  (read-only)  │            │  (metadata)  │
   └──────────┘              └───────┬───────┘            └──────────────┘
                                     │
     ┌──────────┬──────────┬─────────┼─────────┬──────────┬──────────┐
     │          │          │         │         │          │          │
   GTM        GA4      Google Ads   Meta    Search    GitHub      CMP
                                            Console
```

**Core principle: the obligation register is the spine, not the integrations.**
Every integration exists to answer specific obligations. If an integration cannot be
mapped to an obligation, it does not belong in the product.

Build the adapters behind a common interface so that a missing integration degrades
gracefully to "Cannot determine" rather than failing the assessment.

```ts
interface IntegrationAdapter {
  id: string;
  requiredScopes: string[];
  obligationsAnswered: string[];        // e.g. ['S6.consentRecord','S6.propagation']
  connect(credentials): Promise<Connection>;
  collect(conn, scope): Promise<Evidence[]>;
  healthCheck(conn): Promise<Status>;
}

interface Evidence {
  obligationId: string;
  verdict: 'Observed' | 'Not observed' | 'Cannot determine';
  confidence: 'high' | 'medium' | 'low';
  source: string;                       // which adapter, which API call
  observedAt: string;
  detail: string;                       // human-readable, no personal data
  raw?: object;                         // config metadata only, never PII
}
```

---

## 4. Integration catalogue

Each section states: what it answers, what to read, **what never to read**, and the
acceptance test.

---

### 4.1 Google Tag Manager — build this first

**Highest value of every integration.** Phase 1 sees what fired on the pages it
visited. GTM shows what is *configured* across every trigger and every page, and
whether consent settings are attached to each tag.

**Obligations answered:** consent gating completeness, tag inventory, consent-mode
configuration.

**API:** Tag Manager API v2. Scope: `https://www.googleapis.com/auth/tagmanager.readonly`

**Read:**
- All containers, workspaces, published versions
- Every tag: type, vendor, firing triggers, `consentSettings` block
- Built-in and custom variables that carry identifiers
- Version history — when consent settings were last changed

**Never read:** nothing here contains personal data, but do not store custom HTML
tag bodies verbatim; they sometimes contain hardcoded values. Store a hash and a
classification instead.

**Derived findings:**
- Tags with no `consentSettings` configured
- Tags firing on triggers that precede consent
- Tags configured to send user-provided data (Enhanced Conversions, Customer Match)
- Difference between what Phase 1 observed and what GTM has configured — **a large
  gap here usually means hardcoded tags outside the container, which is the reason
  the GitHub integration exists**

**Acceptance test:** on a container with 20 tags where 5 have consent settings, the
adapter reports exactly 5 configured and 15 not, with tag names and trigger names,
and no false positives against a manual export.

---

### 4.2 GA4

**Obligations answered:** retention configuration, consent-mode operation, data
collected, onward sharing.

**API:** Google Analytics Admin API v1. Scope:
`https://www.googleapis.com/auth/analytics.readonly`

**Read:**
- Property settings: `dataRetentionSettings`, `resetUserDataOnNewActivity`
- Google Signals status
- Linked services: Ads, BigQuery, Search Console, Display & Video
- Custom dimensions and metrics — **names and scopes only**
- Consent-mode diagnostic status where exposed
- Data streams and enhanced measurement settings

**Never read:** report data, user-level data, the User Activity API, any endpoint
returning individual behaviour. **The tool has no legitimate need for a single row of
event data.** Reading it would make us a processor of behavioural data for no
analytical gain.

**Derived findings:**
- Retention set to the maximum with no documented justification
- Google Signals enabled — cross-device identification with consent implications
- BigQuery export active — data leaving into a second system that also needs to be in scope
- Custom dimensions whose *names* suggest personal data is being passed as parameters
  (flag for human review; never inspect the values)

**Acceptance test:** correctly reports retention months, Signals state and every
linked service on a test property, and makes zero calls to any reporting endpoint —
verify from the audit log.

---

### 4.3 Google Ads

**Obligations answered:** lawful basis for audience uploads, consent-mode signals,
onward sharing.

**API:** Google Ads API. Read-only via a manager account link.

**Read:**
- Customer Match user list metadata: name, size, upload date, membership duration
- Conversion actions and whether Enhanced Conversions is enabled
- Consent-mode signal state as reported in diagnostics
- Data Manager API connections (the legacy Customer Match upload path was
  discontinued in April 2026 — flag any client still attempting it)
- Audience sharing across accounts

**Never read:** the contents of any user list. Ever. Metadata only.

**Derived findings:**
- Customer Match lists in use — each one needs a recorded consent basis, and almost
  no brand has one. **This is frequently the single most uncomfortable finding in the
  entire assessment and it is invisible from outside.**
- Enhanced Conversions active — hashed personal data being transmitted
- Lists uploaded before the DPDP Rules with no refresh since

**Acceptance test:** enumerates every Customer Match list with size and upload date,
and the audit log shows no call that would return list membership.

---

### 4.4 Meta

**Obligations answered:** same as Google Ads — audience basis, onward sharing.

**API:** Marketing API, read-only. Requires app review and Business Manager access.

**Read:**
- Custom Audience metadata: name, subtype, approximate size, origin, sharing
- Pixel and Conversions API configuration
- Advanced matching settings
- Data sharing between Business Manager accounts
- Offline event set configuration

**Never read:** audience membership, event payloads, user identifiers.

**Derived findings:**
- Customer-list custom audiences with no recorded basis
- Advanced matching sending hashed personal data
- Audiences shared with agencies or partners — a processor relationship that needs a
  contract
- Conversions API sending server-side data — often invisible to marketing teams

**Acceptance test:** enumerates audiences with subtype and origin, correctly
distinguishes customer-list audiences from website audiences, and reads no membership.

**Note on effort:** Meta app review is slow and the permissions are broad. Consider
deferring this adapter until GTM, GA4 and Google Ads are proven.

---

### 4.5 Search Console

**Obligations answered:** minor. Discovery of properties in scope.

**API:** Search Console API. Scope: `webmasters.readonly`

**Read:** verified property list, sitemaps.

**Purpose:** finds subdomains and properties the client forgot to mention. Scope
completeness is a real problem in assessments and this solves part of it cheaply.

**Never read:** search query data.

**Acceptance test:** returns the full verified property list, and the assessment
scope sheet is updated with any property not previously declared.

---

### 4.6 GitHub — build this last, and scope it narrowly

Lowest value per unit of effort, most invasive access, hardest to parse reliably.
Build it only after the others are proven and only if clients will actually grant it.

**Obligations answered:** hardcoded collection outside the tag manager, consent
checks in app code, personal data in logs.

**API:** GitHub REST/GraphQL, read-only, installed as a GitHub App scoped to named
repositories. Never a personal access token.

**Read — static analysis only:**
- Tracker SDK initialisation outside the tag manager (Firebase, Branch, AppsFlyer,
  CleverTap, MoEngage, Meta SDK, Google Analytics SDK)
- Whether SDK initialisation is gated on a consent check
- Permission declarations in `AndroidManifest.xml` and `Info.plist`
- Form field definitions in app code that collect personal data
- Logging statements that write personal data to console or crash reporting
- Third-party SDK inventory from `package.json`, `Podfile`, `build.gradle`

**Never do:**
- Clone repositories to persistent storage
- Store source code
- Read `.env` files, secrets, or configuration containing credentials
- Any write operation

**Implementation note:** run the analysis in an ephemeral container, hold the code in
memory or on a tmpfs, emit findings, destroy the container. The evidence store gets
the finding and a file path, never the code.

**Derived findings:**
- An SDK initialising in `Application.onCreate()` before any consent check — the
  mobile equivalent of a tag firing before the banner
- Personal data written to logs
- SDKs present in the manifest that nobody in marketing knows about

**Acceptance test:** on a seeded test repository with three planted issues, finds all
three, reports zero false positives, and leaves no code on disk after the run.

---

### 4.7 Consent platform APIs

If the client licenses OneTrust, CookieYes, Securiti or similar, read their
configuration rather than inferring it.

**Read:** purpose taxonomy, cookie categorisation, geolocation rules, banner
configuration, consent-record schema, integration list.

**Never read:** individual consent records. **This is the sharpest instance of the
rule.** A consent record is personal data about a specific individual. We assess
whether records are being kept, not what they say.

**Derived findings:** purposes configured versus purposes actually in use; tags
categorised versus tags observed by Phase 1; geolocation rules that exempt India.

---

## 5. Cross-source reconciliation — where the real value is

Any single integration is a config dump. The value is in the contradictions between
them, and this is what nobody else does.

Build these as explicit rules:

| Rule | Sources | Why it matters |
|---|---|---|
| Tags in GTM vs tags observed by Phase 1 | GTM + scanner | A gap means hardcoded tags outside the container |
| GTM consent settings vs consent signals observed | GTM + scanner | Configured but not working, or working but not configured |
| GA4 linked services vs declared scope | GA4 + scope sheet | Data flowing somewhere nobody declared |
| Customer Match lists vs consent purposes | Ads + CMP | Uploads with no matching purpose |
| App SDKs vs declared marketing platforms | GitHub + scope sheet | SDKs marketing does not know about |
| Search Console properties vs declared properties | SC + scope sheet | Scope gaps |
| CMP purposes vs GTM tag categories | CMP + GTM | Tags in no category are ungoverned |

Each contradiction becomes a finding with both sources cited. **A finding backed by
two independent sources is the strongest artifact the platform can produce** and
should be presented as such.

---

## 6. Evidence and confidence

Reuse `validate.js` from Phase 1 — the same discipline applies.

- Every finding carries a confidence level and names its source
- LOW confidence never produces "Not observed", only "Cannot determine"
- A finding derived from two agreeing sources is upgraded to HIGH
- A finding where two sources disagree is flagged for human review, never auto-resolved
- Anything a human overrode is recorded, with the reason

**Track the override rate.** If humans are correcting more than about 10% of
findings, the automation is not ready for that finding type. This is the single most
useful health metric for the platform.

---

## 7. Build order

Sequenced by value per unit of effort. Do not reorder without good reason.

| Sprint | Deliverable | Why here |
|---|---|---|
| 0 | DPA, retention policy, secret store, audit log, per-client isolation | Nothing else may start first |
| 1 | Adapter interface, evidence store, obligation register, OAuth shell | The spine |
| 2 | **GTM adapter** | Highest value, cleanest data, no personal data risk |
| 3 | **Reconciliation: GTM vs Phase 1 scanner** | The first genuinely differentiated output |
| 4 | GA4 adapter | Retention and linkage findings |
| 5 | Google Ads adapter | Customer Match findings — commercially the sharpest |
| 6 | Search Console adapter | Cheap, improves scope completeness |
| 7 | Report generation into the existing register and templates | Ties back to what already exists |
| 8 | CMP adapters | Only for clients who have one |
| 9 | Meta adapter | Slow app review — start the paperwork at sprint 5 |
| 10 | GitHub adapter | Last, narrow, ephemeral |

**Ship after sprint 3.** GTM plus reconciliation against the Phase 1 scanner is
already a materially better product than Phase 1 alone, and it will teach you what
clients actually react to before more is built.

---

## 8. Guidance for building this with Claude Code / Cursor

**Give the model this document as context.** It contains the constraints that matter;
without them the model will happily write code that stores event data.

**Work adapter by adapter.** One adapter per session, with its acceptance test written
before the adapter. These are well-defined, self-contained units and that is where
the tooling performs best.

**Write the "never read" list as tests, not comments.** For each adapter, add a test
that fails if the adapter calls a forbidden endpoint. Mock the API client, assert on
the call list. A comment saying "never read report data" will eventually be ignored;
a failing test will not.

**Ask for the audit-log entry alongside every API call.** It is easy to add while
writing the call and tedious to retrofit across a codebase.

**Be explicit about PII discarding in the adapter layer.** Prompt for it directly:
*"strip any field that could contain personal data before returning; return only
configuration metadata."* Then verify by inspecting a real response.

**Do not let it invent obligation mappings.** The obligation register comes from the
counsel-reviewed working file. The model should map to existing IDs, not create new
ones.

---

## 9. Definition of done, per adapter

An adapter is finished when all seven are true:

1. Read-only scopes only, each mapped to a named obligation in the register
2. A passing test proving it does not call any forbidden endpoint
3. A passing test proving no personal data reaches the evidence store
4. Audit log entry emitted for every API call
5. Graceful degradation — missing access produces "Cannot determine", not an error
6. Acceptance test from Section 4 passing against a real account
7. Findings render correctly into the existing report template

---

## 10. Open questions to resolve before build

1. Who owns the DPA template, and has counsel reviewed it?
2. Where does the evidence store live, and in which region?
3. What is the retention period for evidence after an engagement ends?
4. Do we hold a Google Ads manager account suitable for read-only client links?
5. Is anyone prepared to sponsor the Meta app review process?
6. Will clients realistically grant repository access, or is 4.6 theoretical?
7. Who is the named accountable owner for the platform as a processor?

*Question 7 is the one that will be asked first by any client's legal team.*
