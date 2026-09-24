import asyncio
from sqlalchemy import select
from app.db.session import async_session_factory
from app.db.models.connection import Connection, ConnectionStatus

async def main():
    async with async_session_factory() as session:
        result = await session.execute(select(Connection).where(Connection.provider == "meta"))
        connections = result.scalars().all()
        for c in connections:
            print(f"ID: {c.id}")
            print(f"Status: {c.status}")
            print(f"Client ID: {c.client_id}")
            print(f"Resource ID: {c.resource_id}")
            print(f"Connected At: {c.connected_at}")
            print(f"Meta: {c.provider_metadata}")
            print("-" * 40)

if __name__ == "__main__":
    asyncio.run(main())
