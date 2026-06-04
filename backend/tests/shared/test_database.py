"""Unit tests for the DB-core helpers that need no live database."""

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.shared import database
from src.shared.database import NAMING_CONVENTION, TENANT_GUC, Base, set_tenant_context


def test_base_uses_naming_convention() -> None:
    assert Base.metadata.naming_convention == NAMING_CONVENTION


async def test_set_tenant_context_uses_local_set_config() -> None:
    session = AsyncMock(spec=AsyncSession)
    org_id = uuid4()

    await set_tenant_context(cast(AsyncSession, session), org_id)

    session.execute.assert_awaited_once()
    _, params = session.execute.await_args.args
    assert params == {"key": TENANT_GUC, "value": str(org_id)}


def test_module_getattr_rejects_unknown_attribute() -> None:
    with pytest.raises(AttributeError):
        _ = database.does_not_exist
