"""Seed/import the obligation register into the database.

Usage:
    python -m app.cli.seed_obligations [--json-file path/to/register.json]

If --json-file is provided, obligations are loaded from the JSON file.
Otherwise, the built-in OBLIGATIONS list from app.domain.obligations is used.

This is the mechanism for replacing the draft register with the
counsel-reviewed final register:
1. Update REGISTER_VERSION in app/domain/obligations.py (or supply a JSON file).
2. Run this script.
3. Obligations are upserted: new codes are inserted, existing codes are updated,
   codes removed from the register are marked active=False (never deleted).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path


async def seed_obligations(json_file: str | None = None) -> None:
    """Seed obligations from the register into the database."""
    from app.db.session import async_session_factory
    from app.db.models.obligation import Obligation, ObligationCategory, ObligationSeverity
    from app.domain.obligations import (
        OBLIGATIONS, REGISTER_VERSION,
        validate_obligation_register,
    )
    from sqlalchemy import select

    # Load register
    if json_file:
        with open(json_file, "r", encoding="utf-8") as f:
            register_data = json.load(f)
        register = register_data.get("obligations", register_data)
        version = register_data.get("version", REGISTER_VERSION)
        if not isinstance(register, list):
            print("ERROR: JSON file must contain a list of obligations or an object with 'obligations' key.")
            sys.exit(1)
    else:
        register = OBLIGATIONS
        version = REGISTER_VERSION

    # Validate
    errors = validate_obligation_register(register)
    if errors:
        print("VALIDATION ERRORS:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"Register version: {version}")
    print(f"Obligations in register: {len(register)}")

    # Upsert
    inserted = 0
    updated = 0
    deactivated = 0

    async with async_session_factory() as session:
        result = await session.execute(select(Obligation))
        existing = {o.code: o for o in result.scalars().all()}

        register_codes = set()

        for ob in register:
            code = ob["code"]
            register_codes.add(code)

            if code in existing:
                db_obl = existing[code]
                if db_obl.version != version or not db_obl.active:
                    db_obl.title = ob["title"]
                    db_obl.description = ob.get("description", "")
                    db_obl.category = ObligationCategory(ob["category"])
                    db_obl.severity = ObligationSeverity(ob["severity"])
                    db_obl.dpdp_section = ob.get("dpdp_section", "")
                    db_obl.source_reference = ob.get("source_reference")
                    db_obl.version = version
                    db_obl.active = True
                    updated += 1
                    print(f"  UPDATED: {code}")
            else:
                new_obl = Obligation(
                    id=uuid.uuid4(),
                    code=code,
                    title=ob["title"],
                    description=ob.get("description", ""),
                    category=ObligationCategory(ob["category"]),
                    severity=ObligationSeverity(ob["severity"]),
                    active=True,
                    dpdp_section=ob.get("dpdp_section", ""),
                    version=version,
                    source_reference=ob.get("source_reference"),
                )
                session.add(new_obl)
                inserted += 1
                print(f"  INSERTED: {code}")

        # Deactivate codes no longer in register (never delete)
        for code, db_obl in existing.items():
            if code not in register_codes and db_obl.active:
                db_obl.active = False
                deactivated += 1
                print(f"  DEACTIVATED: {code}")

        await session.commit()

    print(f"\nDone. Inserted: {inserted}, Updated: {updated}, Deactivated: {deactivated}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the obligation register into the database.")
    parser.add_argument(
        "--json-file",
        type=str,
        default=None,
        help="Path to a JSON file containing the obligation register. If omitted, uses the built-in register.",
    )
    args = parser.parse_args()
    asyncio.run(seed_obligations(args.json_file))


if __name__ == "__main__":
    main()
