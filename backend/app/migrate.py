"""Tiny additive migration: create tables, then add any model columns missing
from existing tables. Never drops or alters anything — safe on live data."""
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateColumn


def ensure_schema(engine, base) -> list[str]:
    base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = str(CreateColumn(column).compile(dialect=engine.dialect))
                # existing rows can't satisfy NOT NULL / PK additions; add as nullable
                ddl = ddl.replace(" NOT NULL", "")
                conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))
                default = getattr(column.default, "arg", None) if column.default is not None else None
                if isinstance(default, (str, int, float, bool)):  # backfill rows that existed before the column
                    conn.execute(text(f"UPDATE {table.name} SET {column.name} = :v WHERE {column.name} IS NULL"), {"v": default})
                added.append(f"{table.name}.{column.name}")
    return added
