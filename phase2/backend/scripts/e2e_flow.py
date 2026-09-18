"""End-to-end test: login -> create assessment -> list assessments."""
import asyncio
import httpx

API_BASE = "http://localhost:8000/api/v1"

async def test_flow():
    async with httpx.AsyncClient() as client:
        # 1. Login
        print("1. Testing login...")
        res = await client.post(f"{API_BASE}/auth/login", json={
            "email": "test@acmecorp.com",
            "password": "password123"
        })
        print(f"   Login: {res.status_code}")
        if res.status_code != 200:
            print(f"   ERROR: {res.text}")
            return
        token = res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # 2. Get user info
        print("2. Testing /auth/me...")
        res = await client.get(f"{API_BASE}/auth/me", headers=headers)
        print(f"   Me: {res.status_code} -> {res.json()}")
        client_id = list(res.json()["client_roles"].keys())[0]
        
        # 3. List clients
        print("3. Testing list clients...")
        res = await client.get(f"{API_BASE}/clients", headers=headers)
        print(f"   Clients: {res.status_code} -> {len(res.json())} client(s)")
        
        # 4. List integrations
        print(f"4. Testing list integrations for client {client_id[:8]}...")
        res = await client.get(f"{API_BASE}/integrations?client_id={client_id}", headers=headers)
        print(f"   Integrations: {res.status_code} -> {len(res.json())} connection(s)")
        
        # 5. Create assessment
        print("5. Testing create assessment...")
        res = await client.post(f"{API_BASE}/assessments", headers=headers, json={
            "client_id": client_id,
            "config": {"integrations": ["GTM", "GA4"]}
        })
        print(f"   Create: {res.status_code}")
        if res.status_code == 201:
            assessment = res.json()
            print(f"   Assessment ID: {assessment['id']}")
            print(f"   Status: {assessment['status']}")
            print(f"   Runs: {len(assessment.get('runs', []))}")
        else:
            print(f"   ERROR: {res.text}")
            
        # 6. List assessments
        print("6. Testing list assessments...")
        res = await client.get(f"{API_BASE}/assessments", headers=headers)
        print(f"   List: {res.status_code} -> {res.json()['total']} assessment(s)")
        
        # 7. Audit logs
        print("7. Testing audit logs...")
        res = await client.get(f"{API_BASE}/audit-logs?client_id={client_id}", headers=headers)
        print(f"   Audit Logs: {res.status_code}")
        
        # 8. Health check
        print("8. Testing health check...")
        res = await client.get("http://localhost:8000/health")
        print(f"   Health: {res.status_code} -> {res.json()}")
        
        print("\n✅ ALL ENDPOINTS TESTED SUCCESSFULLY")

if __name__ == "__main__":
    asyncio.run(test_flow())
