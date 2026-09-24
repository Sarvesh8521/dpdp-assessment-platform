import asyncio
from app.db.session import async_session_factory
from app.integrations.meta.adapter import MetaAdapter
from app.integrations.base.adapter import ConnectionInfo
from app.db.models.connection import Connection
from sqlalchemy import select

async def main():
    async with async_session_factory() as session:
        conn = (await session.execute(
            select(Connection)
            .where(Connection.provider == "meta")
            .where(Connection.resource_id.isnot(None))
        )).scalars().first()

        if not conn:
            print("No connection found")
            return

        adapter = MetaAdapter()
        conn_info = ConnectionInfo(
            provider="meta",
            scopes=[],
            connected_at=conn.created_at,
            expires_at=None,
            metadata=conn.provider_metadata or {}
        )
        
        class DummyCallback:
            def __call__(self, *args, **kwargs):
                pass
                
        result = await adapter.collect(conn_info, ad_account_id=(conn.provider_metadata or {}).get("ad_account_id"), audit_callback=DummyCallback())
        print(f"Errors: {result.errors}")
        for ev in result.evidence:
            print(f"- {ev.obligation_id} ({ev.verdict}): {ev.detail}")

asyncio.run(main())
