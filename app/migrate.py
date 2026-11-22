"""Tiny, safe schema upgrade so existing SQLite databases keep their users and videos."""
from sqlalchemy import inspect, text

NEW_COLUMNS = {
    "user": [("email", "VARCHAR(120)")],
    "video": [("duration", "INTEGER"), ("upload_id", "VARCHAR(256)"),
              ("part_size", "INTEGER"), ("parts", "INTEGER")],
}


def ensure_schema(db):
    insp = inspect(db.engine)
    with db.engine.begin() as conn:
        for table, cols in NEW_COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in have:
                    conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email ON "user" (email)'))
