import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://visually:visually@localhost:5432/visually_platform"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    # Discard stale pool connections before handing them to a request.
    # Without this, a pipeline session that dies mid-transaction returns the
    # connection to the pool in a dirty state, causing the next request to fail
    # with "cannot use Connection.transaction() in a manually started transaction".
    pool_pre_ping=True,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            # Always rollback on exception so the connection is returned to the
            # pool in a clean state — prevents dirty connections from causing
            # "manually started transaction" errors in subsequent requests.
            await session.rollback()
            raise
