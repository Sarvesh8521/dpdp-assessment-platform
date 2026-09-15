import asyncio
import uuid
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.config import get_settings
from app.auth.dependencies import pwd_context
from app.db.models.client import Client, ClientStatus
from app.db.models.user import User, UserStatus
from app.db.models.client_user import ClientUser, UserRole


async def seed_data():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        # Clear existing data
        await session.execute(text("TRUNCATE users CASCADE"))
        await session.execute(text("TRUNCATE clients CASCADE"))
        
        # Create user
        user_id = uuid.uuid4()
        hashed_pw = pwd_context.hash("password123")
        user = User(
            id=user_id,
            identity="test@acmecorp.com",
            hashed_password=hashed_pw,
            display_label="Test User",
            status=UserStatus.ACTIVE
        )
        session.add(user)
        
        # Create client
        client_id = uuid.uuid4()
        client = Client(
            id=client_id,
            name="Acme Corp Test",
            status=ClientStatus.ACTIVE,
            industry="Technology",
            website="https://acmecorp.com"
        )
        session.add(client)
        
        # Create relationship
        client_user = ClientUser(
            user_id=user_id,
            client_id=client_id,
            role=UserRole.PLATFORM_ADMIN
        )
        session.add(client_user)
        
        try:
            await session.commit()
            print(f"SEEDED Client ID: {client_id}")
            print(f"SEEDED User ID: {user_id}")
        except Exception as e:
            await session.rollback()
            print(f"Failed to seed: {e}")

if __name__ == "__main__":
    asyncio.run(seed_data())
