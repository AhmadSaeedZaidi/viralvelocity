"""Safety interlock for Alkyone integration runs.

Alkyone talks to REAL infrastructure (a Postgres database and a HuggingFace
vault). To make sure it can never silently mutate production data, every
Alkyone session starts by calling :func:`assert_not_production`, which
**hard-refuses** (``SystemExit``) the run when the configured ``DATABASE_URL``
or ``HF_DATASET_ID`` matches the known production target.

Production targets are supplied out-of-band (never committed) via:

* ``PLEIADES_PROD_DATABASE_URL`` -- the live Postgres connection string
* ``PLEIADES_PROD_VAULT``        -- the live HuggingFace dataset repo id

Set either to the real production value in your environment / CI secrets and
Alkyone will refuse to start if it is pointed at them. This is intentionally a
hard stop (not a skip): running integration tests against production is a
data-integrity incident, not a skipped test.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("alkyone.guard")


def _norm(value: str | None) -> str:
    return (value or "").strip().rstrip("/").lower()


def _matches(prod: str | None, candidate: str | None) -> bool:
    prod_n, cand_n = _norm(prod), _norm(candidate)
    if not prod_n or not cand_n:
        return False
    return cand_n == prod_n


def assert_not_production() -> None:
    """Raise ``SystemExit`` if Alkyone is pointed at production infrastructure.

    Called at session start (conftest ``pytest_configure`` + ``make guard``)
    before any test connects. Because the refusal is a hard exit, a CI job
    fails loudly instead of quietly running against production.
    """
    prod_db = os.getenv("PLEIADES_PROD_DATABASE_URL")
    db = os.getenv("DATABASE_URL")
    if _matches(prod_db, db):
        _refuse(
            "DATABASE_URL points at the PRODUCTION database "
            f"({_norm(db)}). Alkyone must run against an isolated test DB. "
            "Point DATABASE_URL at a test instance, or (if you really mean it) "
            "unset PLEIADES_PROD_DATABASE_URL."
        )

    prod_vault = os.getenv("PLEIADES_PROD_VAULT")
    vault = os.getenv("HF_DATASET_ID")
    if _matches(prod_vault, vault):
        _refuse(
            "HF_DATASET_ID points at the PRODUCTION vault "
            f"({_norm(vault)}). Alkyone must run against an isolated test vault. "
            "Point HF_DATASET_ID_TEST at a test dataset, or unset PLEIADES_PROD_VAULT."
        )

    logger.info("Alkyone guard: DATABASE_URL / HF_DATASET_ID are NOT production. Proceeding.")


def _refuse(reason: str) -> None:
    msg = f"ALKYONE REFUSAL: {reason}"
    logger.error(msg)
    sys.exit(f"\n{msg}\n")


if __name__ == "__main__":
    assert_not_production()
