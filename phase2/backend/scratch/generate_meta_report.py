import asyncio
import json
import uuid
import sys
from sqlalchemy import select
from app.db.session import async_session_factory
from app.db.models.connection import Connection, ConnectionStatus
from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.services.assessment_service import AssessmentService
from app.services.report_generator import ReportGeneratorService

async def main():
    async with async_session_factory() as session:
        result = await session.execute(
            select(Connection).where(
                Connection.provider == "meta", 
                Connection.status == ConnectionStatus.CONNECTED,
                Connection.resource_id.isnot(None),
                Connection.resource_id != "123456789_business"
            ).order_by(Connection.connected_at.desc())
        )
        conn = result.scalars().first()
        if not conn:
            print("No active Meta connection found.")
            return

        client_id = conn.client_id
        print(f"Using Client ID: {client_id}")
        
        # Create Assessment
        service = AssessmentService(session, client_id)
        config = AssessmentConfig(integrations=["meta"])
        data = AssessmentCreate(client_id=client_id, config=config)
        
        from app.db.models.user import User
        user = (await session.execute(select(User))).scalars().first()
        if not user:
            print("No users found.")
            return
        user_id = user.id
        assessment = await service.create_assessment(data, user_id)
        print(f"Assessment created: {assessment.id}")

        # Generate report
        report_service = ReportGeneratorService(session, client_id)
        report = await report_service.generate_report(assessment.id)
        print(f"Report generated: {report.artifact_ref}")

        with open(report.artifact_ref, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        unresolved = []
        findings = report_data.get("individual_reports", {}).get("meta", {}).get("findings", [])
        for f in findings:
            if f["verdict"] == "Cannot determine" and "not observed" not in f.get("reason", "").lower():
                unresolved.append(f"{f['obligation_code']}: {f['detail']} - {f['reason']}")
            
        print("\n=== Meta Findings Verification ===")
        if unresolved:
            print("FAILED: Found unresolved findings (Cannot determine):")
            for u in unresolved:
                print(f"- {u}")
        else:
            print("SUCCESS: No unresolved findings caused by implementation errors!")

        for f in findings:
            print(f"[{f['verdict']}] {f['obligation_code']}: {f['detail']}")

if __name__ == "__main__":
    asyncio.run(main())
