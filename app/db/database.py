from sqlalchemy import URL, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.base.config import pg_settings

DATABASE_URL: URL = URL.create(
    "postgresql+psycopg",
    username=pg_settings.PG_USER,
    password=pg_settings.PG_PASSWORD,
    host=pg_settings.PG_HOST,
    port=pg_settings.PG_PORT,
    database=pg_settings.DB_NAME,
)

engine = create_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


Base = declarative_base()
