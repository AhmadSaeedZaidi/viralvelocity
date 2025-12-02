import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Get DB URL from Environment Variables
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    print("WARNING: DATABASE_URL not found. Database operations will fail.")

# 2. Create the Engine
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# 3. Session Factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 4. Base Model
Base = declarative_base()