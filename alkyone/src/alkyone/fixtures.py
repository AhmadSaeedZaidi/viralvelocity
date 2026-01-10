import pytest
import pytest_asyncio
import os
import logging
import asyncio
from typing import AsyncGenerator

# We import the infrastructure directly from Atlas
from atlas.db import db
from atlas.config import settings

# --- ENVIRONMENT OVERRIDES ---
# Crucial: Ensure we never accidentally nuke Production
os.environ["ENV"] = "test"
os.environ["COMPLIANCE_MODE"] = "False"

logger = logging.getLogger("alkyone.fixtures")

@pytest_asyncio.fixture(scope="session")
async def system_init():
    """
    Session-level setup.
    Initializes the DB connection pool once for the whole test suite.
    """
    logger.info("Alkyone: Initializing System for Testing...")
    await db.initialize()
    yield
    await db.close()
    logger.info("Alkyone: System Teardown Complete.")

@pytest_asyncio.fixture(scope="function")
async def fresh_db(system_init) -> AsyncGenerator:
    """
    Function-level fixture.
    Wipes and Re-Provisions the DB schema before EVERY test function.
    This ensures total test isolation.
    """
    async with db.get_connection() as conn:
        # 1. Nuke the world (DROP SCHEMA public)
        # We use CASCADE to kill all tables, views, and extensions in one go.
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        
        # 2. Re-enable extensions (pgvector gets dropped with schema)
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        
        # 3. Provision Schema using Atlas's own SQL definition
        # We look up the file location dynamically from the installed package
        import atlas.schema
        schema_path = os.path.join(os.path.dirname(atlas.schema.__file__), "schema.sql")
        
        if not os.path.exists(schema_path):
            raise FileNotFoundError(f"Could not find schema.sql at {schema_path}")

        with open(schema_path, "r") as f:
            sql_script = f.read()
            await conn.execute(sql_script)
            
    yield
    # No teardown needed; the next test will nuke it anyway.