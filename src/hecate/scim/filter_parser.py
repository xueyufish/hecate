"""SCIM filter parser — translate SCIM filter syntax to SQLAlchemy queries.

Supports eq, co, sw, and operators for filtering UserModel queries.
"""

from __future__ import annotations

import re

from sqlalchemy import Select
from sqlalchemy.sql.elements import ColumnElement

from hecate.models.user import UserModel


def apply_scim_filter(
    stmt: Select,
    filter_str: str | None,
) -> Select:
    """Apply a SCIM filter to a SQLAlchemy select statement.

    Args:
        stmt: Base SQLAlchemy select statement.
        filter_str: SCIM filter string (e.g., 'userName eq "john@example.com"').

    Returns:
        Modified select statement with filter conditions.
    """
    if not filter_str:
        return stmt

    filter_str = filter_str.strip()

    # Simple equality: userName eq 'value'
    eq_match = re.match(r"(\w+)\s+eq\s+['\"](.+?)['\"]", filter_str, re.IGNORECASE)
    if eq_match:
        attr, value = eq_match.groups()
        column = _get_column(attr)
        if column is not None:
            return stmt.where(column == value)

    # Contains: userName co 'value'
    co_match = re.match(r"(\w+)\s+co\s+['\"](.+?)['\"]", filter_str, re.IGNORECASE)
    if co_match:
        attr, value = co_match.groups()
        column = _get_column(attr)
        if column is not None:
            return stmt.where(column.contains(value))

    # Starts with: userName sw 'value'
    sw_match = re.match(r"(\w+)\s+sw\s+['\"](.+?)['\"]", filter_str, re.IGNORECASE)
    if sw_match:
        attr, value = sw_match.groups()
        column = _get_column(attr)
        if column is not None:
            return stmt.where(column.startswith(value))

    # Logical AND: attr1 eq 'v1' and attr2 eq 'v2'
    and_match = re.match(r"(.+?)\s+and\s+(.+)", filter_str, re.IGNORECASE)
    if and_match:
        left, right = and_match.groups()
        stmt = apply_scim_filter(stmt, left.strip())
        stmt = apply_scim_filter(stmt, right.strip())
        return stmt

    return stmt


def _get_column(attr_name: str) -> ColumnElement | None:
    """Map SCIM attribute name to UserModel column."""
    mapping: dict[str, object] = {
        "username": UserModel.email,
        "email": UserModel.email,
        "externalid": UserModel.external_id,
        "displayname": UserModel.display_name,
        "givenname": UserModel.given_name,
        "familyname": UserModel.family_name,
        "active": UserModel.active,
    }
    col = mapping.get(attr_name.lower())
    if col is not None:
        from sqlalchemy.sql.elements import ColumnElement

        if isinstance(col, ColumnElement):
            return col
    return None
