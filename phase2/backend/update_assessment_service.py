import re

with open('app/services/assessment_service.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Cap refreshed token expiry
# Around line 135: if "expires_in" in new_tokens:
refresh_logic = '''                        if "expires_in" in new_tokens:
                            token_data["expires_in"] = new_tokens["expires_in"]
                            # Cap expires_at in DB
                            client_model = await self.session.get(Client, self.client_id)
                            new_expiry = datetime.now(timezone.utc) + timedelta(seconds=new_tokens["expires_in"])
                            if client_model and client_model.engagement_end:
                                new_expiry = min(new_expiry, client_model.engagement_end)
                            if db_conn:
                                db_conn.expires_at = new_expiry'''
content = content.replace(
'''                        if "expires_in" in new_tokens:
                            token_data["expires_in"] = new_tokens["expires_in"]''',
refresh_logic
)

# 2. Add time-box check in run_assessment_task
# After db_conn = conn_result.scalars().first()
timebox_logic = '''                db_conn = conn_result.scalars().first()

                # ---- TIME-BOXED ACCESS CHECK ----
                if db_conn:
                    client_model = await self.session.get(Client, self.client_id)
                    now = datetime.now(timezone.utc)
                    if client_model and client_model.engagement_end and now > client_model.engagement_end:
                        db_conn.status = ConnectionStatus.EXPIRED
                        await self.session.commit()
                        logger.info("engagement_expired_blocking_access", provider=adapter.provider)
                        
                        degraded_evidence = [
                            EvidenceItem(
                                obligation_id=obl_code,
                                verdict=Verdict.CANNOT_DETERMINE,
                                confidence=Confidence.LOW,
                                source=f"{adapter.id}:expired",
                                observed_at=now,
                                detail="Access expired — engagement period ended",
                                raw_metadata={"error": "engagement_expired"}
                            )
                            for obl_code in obl_codes
                        ]
                        for ev_item in degraded_evidence:
                            ev = Evidence(
                                assessment_id=self.assessment_id,
                                client_id=self.client_id,
                                provider=adapter.provider,
                                verdict=ev_item.verdict,
                                confidence=ev_item.confidence,
                                source=ev_item.source,
                                observed_at=ev_item.observed_at,
                                detail=ev_item.detail,
                                sanitized_metadata=ev_item.raw_metadata,
                            )
                            all_db_evidence.append((ev, ev_item.obligation_id))
                        continue
                # ----------------------------------'''

content = content.replace(
'''                db_conn = conn_result.scalars().first()''',
timebox_logic
)


with open('app/services/assessment_service.py', 'w', encoding='utf-8') as f:
    f.write(content)
