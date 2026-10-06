"""Apply the DuckDB schema (idempotent CREATE TABLE IF NOT EXISTS statements)."""

from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent.parent / "configs" / "schema.sql"

# feature_history's primary key is what makes backfill's INSERT OR IGNORE
# idempotent. Every CREATE in schema.sql is IF NOT EXISTS, so a database created
# before the key was added to schema.sql keeps the old, key-less table forever --
# the declaration in schema.sql simply never runs again. The committed
# feature_store.duckdb was in exactly that state, and the consequence was not
# subtle: backfill.py failed on all 91 snapshots with "There are no
# UNIQUE/PRIMARY KEY constraints that refer to this table", so the headline
# backfill command did not work at all on the shipped artifact.
#
# DuckDB cannot add a primary key to an existing table, so the fix is a rebuild:
# copy the rows out, recreate with the key, copy them back. Doing it here rather
# than in a one-off script means anyone holding an old database is repaired the
# next time any entry point calls apply_schema.
_FEATURE_HISTORY_PK = ("entity_id", "feature_version", "event_time")


def _feature_history_needs_pk(client) -> bool:
    exists = client.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'feature_history'"
    )
    if not exists or not exists[0][0]:
        return False  # nothing to migrate; the CREATE below will include the key
    constraints = client.execute(
        "SELECT count(*) FROM duckdb_constraints() "
        "WHERE table_name = 'feature_history' AND constraint_type = 'PRIMARY KEY'"
    )
    return not (constraints and constraints[0][0])


def _rebuild_feature_history(client, create_stmt: str) -> None:
    client.execute(
        "CREATE TABLE feature_history_migrating AS SELECT * FROM feature_history"
    )
    client.execute("DROP TABLE feature_history")
    client.execute(create_stmt)
    # DISTINCT ON the key: a key-less table may already hold duplicates that the
    # primary key would reject, which would abort the migration half-done.
    cols = ", ".join(_FEATURE_HISTORY_PK)
    client.execute(
        "INSERT INTO feature_history SELECT * FROM ("
        f"  SELECT DISTINCT ON ({cols}) * FROM feature_history_migrating"
        ")"
    )
    client.execute("DROP TABLE feature_history_migrating")


def _split_statements(sql: str) -> list[str]:
    """Split schema.sql into statements, ignoring semicolons inside comments.

    Splitting the raw text on ";" looks fine until a line comment contains one,
    at which point the CREATE is cut in half and DuckDB reports "syntax error at
    end of input" pointing at a statement that looks complete. Strip line
    comments first so a semicolon in prose cannot terminate a statement.
    """
    stripped = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [s.strip() for s in stripped.split(";") if s.strip()]


def apply_schema(client) -> None:
    statements = _split_statements(_SCHEMA_PATH.read_text())

    migrate = _feature_history_needs_pk(client)
    create_feature_history = next(
        (
            s
            for s in statements
            if "feature_history" in s and s.upper().startswith("CREATE TABLE")
        ),
        None,
    )
    if migrate and create_feature_history:
        _rebuild_feature_history(client, create_feature_history)

    for stmt in statements:
        client.execute(stmt)
