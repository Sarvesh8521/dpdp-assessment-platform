import asyncio
import json
import uuid
import httpx
from sqlalchemy import select
from app.db.session import async_session_factory
from app.db.models.connection import Connection, ConnectionStatus
from app.services.assessment_service import get_secret_manager

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
            print("No active Meta connection with a resource_id found.")
            return
            
        print(f"Using Connection: {conn.id}")
        print(f"Business ID: {conn.resource_id}")
        
        sm = get_secret_manager()
        secret_json = await sm.get_secret(str(conn.client_id), conn.secret_ref)
        if not secret_json:
            print("No secret found.")
            return
            
        token_data = json.loads(secret_json)
        access_token = token_data.get("access_token")
        
        async with httpx.AsyncClient() as client:
            ad_accounts = await client.get(f"https://graph.facebook.com/v21.0/{conn.resource_id}/owned_ad_accounts?access_token={access_token}&fields=id")
            ad_account_data = ad_accounts.json().get("data", [])
            print("Ad accounts:", ad_accounts.json())
            
            endpoints = [
                f"/v21.0/{conn.resource_id}/datasets",
                f"/v21.0/{conn.resource_id}/owned_datasets",
                f"/v21.0/{conn.resource_id}/offline_conversion_data_sets"
            ]
            
            for ep in endpoints:
                url = f"https://graph.facebook.com{ep}?access_token={access_token}&fields=id,name"
                resp = await client.get(url)
                print(f"\nGET {ep}")
                print(f"Status: {resp.status_code}")
                print(f"Response: {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
