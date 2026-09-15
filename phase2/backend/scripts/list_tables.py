import asyncio
from sqlalchemy import text
from app.db.session import engine

async def list_tables():
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
        print("\n--- Tables in dpdp_phase2 ---")
        for row in result:
            print(f"- {row[0]}")
        print("-----------------------------\n")
            
if __name__ == "__main__":
    asyncio.run(list_tables())
