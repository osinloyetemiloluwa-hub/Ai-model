"""Parameterized SQL query builder for DataSource adapters (ADR-0026 Section D).

safe_to_sql() produces (sql_string, param_list) tuples.
Values are NEVER interpolated — %s / ? / @param placeholders only.
"""
from __future__ import annotations

from typing import Any

from .protocol import FilterExpr, SourceQuery, _VALID_OPS

# Re-export for convenience
__all__ = ["SourceQuery", "FilterExpr", "safe_to_sql"]

# ---------------------------------------------------------------------------
# Dialect-specific placeholder and LIKE escape
# ---------------------------------------------------------------------------

_PLACEHOLDER: dict[str, str] = {
    "psycopg2": "%s",
    "mysql": "%s",
    "sqlite": "?",
    "bigquery": "@p",  # BigQuery uses named params; we'll use positional @p0 … @pN
    "snowflake": "%s",
    "redshift": "%s",
}

_VALID_DIALECTS = frozenset(_PLACEHOLDER)


def _ph(dialect: str, idx: int) -> str:
    """Return the right placeholder token for this dialect and position."""
    if dialect == "bigquery":
        return f"@p{idx}"
    return _PLACEHOLDER[dialect]


# ---------------------------------------------------------------------------
# Op → SQL fragment builder
# ---------------------------------------------------------------------------

def _op_fragment(col: str, op: str, dialect: str, param_idx: int) -> tuple[str, int]:
    """Return (sql_fragment, next_param_idx).

    Raises ValueError for unknown op (already validated in FilterExpr, but
    safe_to_sql may receive raw dicts from untrusted callers).
    """
    if op not in _VALID_OPS:
        raise ValueError(
            f"Unknown FilterExpr op {op!r}. Valid ops: {sorted(_VALID_OPS)}"
        )

    ph = _ph(dialect, param_idx)

    if op == "=":
        return f"{col} = {ph}", param_idx + 1
    if op == "!=":
        return f"{col} != {ph}", param_idx + 1
    if op == "<":
        return f"{col} < {ph}", param_idx + 1
    if op == "<=":
        return f"{col} <= {ph}", param_idx + 1
    if op == ">":
        return f"{col} > {ph}", param_idx + 1
    if op == ">=":
        return f"{col} >= {ph}", param_idx + 1
    if op == "like":
        return f"{col} LIKE {ph}", param_idx + 1
    if op == "is_null":
        # is_null: no parameter consumed
        return f"{col} IS NULL", param_idx
    if op == "in":
        # Value must be a list/tuple; each element gets its own placeholder.
        # We use a sentinel list-of-one for the schema; actual expansion
        # happens at call time — caller must pass list values.
        return f"{col} = {ph}", param_idx + 1  # simplified: single-value form
    if op == "not_in":
        return f"{col} != {ph}", param_idx + 1  # simplified: single-value form

    raise ValueError(f"Unhandled op {op!r}")  # unreachable but satisfies type checkers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def safe_to_sql(
    query: SourceQuery,
    table: str,
    dialect: str,
) -> tuple[str, list[Any]]:
    """Build a parameterized SELECT statement.

    Returns (sql, params) where params is the ordered list of values to bind.
    Values are NEVER formatted into the SQL string — structural SQL-injection defence.

    Args:
        query: The SourceQuery describing columns, filters, ordering, and limit.
        table: Fully-qualified table name (already validated by caller).
        dialect: One of psycopg2 | mysql | sqlite | bigquery | snowflake.

    Raises:
        ValueError: for unknown dialect or unknown op in any FilterExpr.
    """
    if dialect not in _VALID_DIALECTS:
        raise ValueError(
            f"Unknown SQL dialect {dialect!r}. Valid: {sorted(_VALID_DIALECTS)}"
        )

    # Column list
    if query.columns:
        col_list = ", ".join(query.columns)
    else:
        col_list = "*"

    parts: list[str] = [f"SELECT {col_list} FROM {table}"]
    params: list[Any] = []
    param_idx = 0

    # WHERE clause
    where_fragments: list[str] = []
    for fexpr in query.filters:
        frag, param_idx = _op_fragment(fexpr.col, fexpr.op, dialect, param_idx)
        where_fragments.append(frag)
        if fexpr.op != "is_null":
            params.append(fexpr.value)

    if where_fragments:
        parts.append("WHERE " + " AND ".join(where_fragments))

    # ORDER BY
    if query.order_by:
        parts.append(f"ORDER BY {query.order_by}")

    # LIMIT
    if query.limit is not None:
        lim_ph = _ph(dialect, param_idx)
        parts.append(f"LIMIT {lim_ph}")
        params.append(query.limit)
        param_idx += 1

    sql = " ".join(parts)
    return sql, params
