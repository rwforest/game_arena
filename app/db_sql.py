from __future__ import annotations
import os
from typing import Optional
from urllib.parse import urlparse
try:
    from databricks import sql
except Exception: # Keep import optional for local runs
    sql = None # type: ignore
CATALOG = os.environ.get("GA_SQL_CATALOG", "lichess")
SCHEMA = os.environ.get("GA_SQL_SCHEMA", "game_arena")
TABLE = os.environ.get("GA_SQL_TABLE", "chess_games")
# Build fully qualified name
if CATALOG:
    FQN = f"{CATALOG}.{SCHEMA}.{TABLE}"
    SCHEMA_FQN = f"{CATALOG}.{SCHEMA}"
else:
    FQN = f"{SCHEMA}.{TABLE}"
    SCHEMA_FQN = SCHEMA
DDL_STATEMENTS: list[str] = []
if CATALOG:
    DDL_STATEMENTS.append(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
DDL_STATEMENTS.append(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_FQN}")
DDL_STATEMENTS.append(
    f"""
CREATE TABLE IF NOT EXISTS {FQN} (
  experiment_id STRING,
  run_id STRING,
  event_ts TIMESTAMP,
  white STRING,
  black STRING,
  model_white STRING,
  model_black STRING,
  result STRING,
  pgn STRING
) USING DELTA
"""
)
INSERT_SQL = f"""
INSERT INTO {FQN} (experiment_id, run_id, event_ts, white, black, model_white, model_black, result, pgn)
VALUES (?, ?, current_timestamp(), ?, ?, ?, ?, ?, ?)
"""
def _resolve_sql_connection_params() -> dict:
    """Resolve Databricks SQL connection params from standard Apps env vars.
    Priority:
    - Host: DATABRICKS_HOST, else parse from DATABRICKS_WORKSPACE_URL
    - HTTP Path: DATABRICKS_SQL_HTTP_PATH, else build from DATABRICKS_WAREHOUSE_ID
    - Token: DATABRICKS_TOKEN
    """
    host = os.environ.get("DATABRICKS_HOST")
    if not host:
        ws_url = os.environ.get("DATABRICKS_WORKSPACE_URL")
        if ws_url:
            parsed = urlparse(ws_url)
            # Strip scheme to get hostname (and optional path)
            host = parsed.netloc or parsed.path.lstrip("/")
    http_path = os.environ.get("DATABRICKS_SQL_HTTP_PATH")
    if not http_path:
        wh = os.environ.get("DATABRICKS_WAREHOUSE_ID")
        if wh:
            http_path = f"/sql/1.0/warehouses/{wh}"
    token = os.environ.get("DATABRICKS_TOKEN")
    return {"server_hostname": host, "http_path": http_path, "access_token": token}
def _connect():
    if sql is None:
        return None
    params = _resolve_sql_connection_params()
    if not params.get("server_hostname") or not params.get("http_path") or not params.get("access_token"):
        # Missing mandatory params, skip silently
        return None
    return sql.connect(**params)
def init_table() -> None:
    if sql is None:
        return
    conn = _connect()
    if conn is None:
        return
    with conn:
        with conn.cursor() as cur:
            for stmt in DDL_STATEMENTS:
                s = stmt.strip().rstrip(";")
                if s:
                    cur.execute(s)
def insert_game(*, experiment_id: str, run_id: str, white: str, black: str, model_white: str, model_black: str, result: str, pgn: str) -> None:
    if sql is None:
        return
    conn = _connect()
    if conn is None:
        return
    with conn:
        with conn.cursor() as cur:
            cur.execute(INSERT_SQL, (experiment_id, run_id, white, black, model_white, model_black, result, pgn))