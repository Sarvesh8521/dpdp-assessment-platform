# Data Retention Policy

This document defines the retention periods for all data stored by the DPDP Assessment Platform, acting as a Data Processor. 
The automated retention service (`app/services/retention_service.py`) actively enforces these periods.

## Purpose
To ensure personal data and assessment metadata are not kept longer than necessary for the purpose of the assessment, complying with the DPDP Act.

## Retention Periods

| Data Element | Default TTL | Justification |
|---|---|---|
| Assessment Runs & Findings | 30 days post-completion | Needed for client review and final report generation. Automatically hard-deleted. |
| Evidence (Metadata) | 30 days post-completion | Required to substantiate findings during the review period. |
| Physical PDF Reports | 30 days post-completion | Client deliverable. Deleted from storage after the grace period. |
| Audit Logs | 365 days | Required for security and compliance audits to prove platform integrity. |
| Client Credentials / Tokens | Immediate upon revocation / end of engagement | Read-only tokens must be actively destroyed when the assessment completes. |

## Deletion Procedure
The system runs a daily background job that sweeps for expired assessments where `completed_at` > TTL. 
The job hard-deletes all associated Findings, Evidence, and Reports, leaving only an Audit Log entry confirming the purge.
