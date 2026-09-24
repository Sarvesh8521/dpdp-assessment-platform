"""Real-Account Acceptance Test Harness

This script provides a controlled environment to run integration adapters against real accounts.
It ensures that:
1. Credentials are provided ephemerally (via env vars) and never stored.
2. The database is not permanently modified (changes are rolled back).
3. Output is formatted for the ACCEPTANCE_CHECKLIST.md.
4. Blocked configurations (e.g., missing permissions) are clearly reported.

Usage:
    python tests/acceptance/real_account_harness.py --adapter <adapter_id> [--client-id <uuid>] [--assessment-id <uuid>]
"""

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pprint import pprint
from typing import Any

from sqlalchemy import select

# Ensure the app path is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.db.session import async_session_factory
from app.db.models.connection import Connection
from app.integrations.registry import get_adapter
from app.integrations.base.adapter import ConnectionInfo
from app.services.finding_service import FindingService


async def run_harness(adapter_id: str, client_id_str: str | None, assessment_id_str: str | None):
    token = os.environ.get("HARNESS_ACCESS_TOKEN")
    if not token:
        print("ERROR: HARNESS_ACCESS_TOKEN environment variable is not set.")
        print("Please provide an ephemeral access token to run the real-account test.")
        print("Example: set HARNESS_ACCESS_TOKEN=your_token_here && python tests/acceptance/real_account_harness.py --adapter gtm")
        sys.exit(1)

    client_id = uuid.UUID(client_id_str) if client_id_str else uuid.uuid4()
    assessment_id = uuid.UUID(assessment_id_str) if assessment_id_str else uuid.uuid4()

    try:
        adapter = get_adapter(adapter_id)
        if adapter is None:
            print(f"ERROR: Unknown integration adapter '{adapter_id}'")
            sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"--- Starting Real-Account Harness for {adapter_id.upper()} ---")
    print(f"Provider: {adapter.provider}")
    print(f"Required Scopes: {', '.join(adapter.required_scopes)}")
    
    # We use an ephemeral connection info object
    conn_info = ConnectionInfo(
        provider=adapter.provider,
        scopes=adapter.required_scopes,
        connected_at=datetime.now(timezone.utc)
    )

    # Note: The adapter requires an access token. In a real scenario, the adapter 
    # gets it via AssessmentService & SecretManager. For the harness, we might need 
    # to mock the SecretManager or patch the adapter's client creation to use our token.
    # We will patch get_secret_manager to return our token.
    from unittest.mock import AsyncMock, patch
    
    mock_secret_manager = AsyncMock()
    mock_secret_manager.get_secret.return_value = token
    
    async with async_session_factory() as session:
        async with session.begin(): # Start a transaction
            try:
                # We will provide an audit callback to print API calls
                def harness_audit_callback(entry: dict[str, Any]):
                    print(f"[AUDIT] {entry.get('method')} {entry.get('url')} - {entry.get('status_code')} (Elapsed: {entry.get('duration_ms')}ms)")
                
                kwargs = {
                    "access_token": token,
                    "assessment_id": str(assessment_id),
                    "client_id": str(client_id),
                    "audit_callback": harness_audit_callback,
                }
                
                resource_id = getattr(args, "resource_id", None)
                if resource_id:
                    conn_info.account_id = resource_id
                    conn_info.metadata["resource_id"] = resource_id
                
                if adapter_id == "google_ads" and resource_id:
                    kwargs["customer_id"] = resource_id
                elif adapter_id == "meta" and resource_id:
                    kwargs["business_id"] = resource_id
                    conn_info.metadata["business_id"] = resource_id
                elif adapter_id == "github" and resource_id:
                    kwargs["repositories"] = [resource_id]
                elif adapter_id in ("search_console", "google_search_console") and resource_id:
                    kwargs["property_url"] = resource_id
                
                print("\n--- Executing Adapter Collection ---")
                
                # Run the adapter collect method
                result = await adapter.collect(
                    connection=conn_info,
                    **kwargs
                )
                
                print("\n--- Collection Results ---")
                print(f"Partial Failure: {result.partial}")
                if result.errors:
                    print(f"Errors: {result.errors}")
                if result.warnings:
                    print(f"Warnings: {result.warnings}")
                    
                print(f"\nEvidence Collected ({len(result.evidence)} items):")
                for item in result.evidence:
                    print(f"\n- Obligation: {item.obligation_id}")
                    print(f"  Verdict:    {item.verdict.value}")
                    print(f"  Confidence: {item.confidence.value}")
                    print(f"  Source:     {item.source}")
                    print(f"  Detail:     {item.detail}")
                    if item.structured_detail:
                        print(f"  Structured: {item.structured_detail.operation} on {item.structured_detail.resource_type}")
                
            except Exception as e:
                print(f"[FAIL] Exception during setup/execution: {e}")
                import traceback
                traceback.print_exc()
                
            finally:
                # We intentionally raise an exception to rollback the transaction
                # just to ensure absolutely nothing was committed.
                await session.rollback()
    
    print("\n--- Execution Complete ---")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-Account Acceptance Test Harness")
    parser.add_argument("--adapter", required=True, help="Adapter ID (e.g., gtm, ga4, google_ads)")
    parser.add_argument("--client-id", help="Optional Client ID UUID")
    parser.add_argument("--assessment-id", help="Optional Assessment ID UUID")
    parser.add_argument("--resource-id", help="Optional Resource ID (e.g. Container ID, Property URL)")
    args = parser.parse_args()

    asyncio.run(run_harness(args.adapter, args.client_id, args.assessment_id))
