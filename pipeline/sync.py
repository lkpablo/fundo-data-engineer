
import argparse
import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyodbc

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ODBC = os.environ.get(
    "SOURCE_ODBC",
    "DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;DATABASE=FundoSource;Trusted_Connection=yes;",
)
WAREHOUSE_PATH = Path(os.environ.get("WAREHOUSE_PATH", REPO_ROOT / "data" / "warehouse.duckdb"))
STAGING_DIR = WAREHOUSE_PATH.parent / "staging"
FAIL_DURING = os.environ.get("FAIL_DURING")
BUCKET_SIZE = 1_000

# The first column of each table is its key. Types are the warehouse types:

TABLES = {
    "Customers": {
        "strategy": "change_tracking",
        "columns": {"CustomerId": "INTEGER", "CustomerRef": "VARCHAR", "FirstName": "VARCHAR", "LastName": "VARCHAR",
                    "Email": "VARCHAR", "Phone": "VARCHAR", "NationalId": "VARCHAR", "DateOfBirth": "DATE",
                    "CreatedAt": "TIMESTAMP", "UpdatedAt": "TIMESTAMP"},
    },
    "Advances": {
        "strategy": "change_tracking",
        "columns": {"AdvanceId": "INTEGER", "CustomerId": "INTEGER", "Amount": "DECIMAL(12,2)", "Status": "VARCHAR",
                    "RequestedAt": "TIMESTAMP", "FundedAt": "TIMESTAMP", "PaidOffAt": "TIMESTAMP",
                    "CreatedAt": "TIMESTAMP", "UpdatedAt": "TIMESTAMP"},
    },
    "Cards": {
        "strategy": "change_tracking",
        "columns": {"CardId": "INTEGER", "CustomerId": "INTEGER", "Brand": "VARCHAR", "Last4": "VARCHAR",
                    "ExpMonth": "SMALLINT", "ExpYear": "SMALLINT", "Status": "VARCHAR",
                    "CreatedAt": "TIMESTAMP", "UpdatedAt": "TIMESTAMP"},
    },
    "Transactions": {
        "strategy": "rowversion",
        "columns": {"TransactionId": "BIGINT", "CustomerId": "INTEGER", "CardId": "INTEGER", "Amount": "DECIMAL(12,2)",
                    "TxnType": "VARCHAR", "TxnDate": "TIMESTAMP"},
    },
    "CustomerHistory": {
        "strategy": "append_only",
        "columns": {"HistoryId": "BIGINT", "CustomerId": "INTEGER", "FieldName": "VARCHAR", "OldValue": "VARCHAR",
                    "NewValue": "VARCHAR", "ChangedAt": "TIMESTAMP", "ChangedBy": "VARCHAR"},
    },
}


def key_of(spec):
    return next(iter(spec["columns"]))


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ----------------------------------------------- warehouse -----------------------------------------------

def setup_warehouse(wh):
    # No PRIMARY KEY on the data tables, as in BigQuery: MERGE keeps keys unique
    
    for name, spec in TABLES.items():
        columns = ", ".join(f"{column} {kind}" for column, kind in spec["columns"].items())
        wh.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns}, "
                   "_is_deleted BOOLEAN NOT NULL DEFAULT false, _deleted_at TIMESTAMP, _loaded_at TIMESTAMP NOT NULL)")
    wh.execute("CREATE TABLE IF NOT EXISTS _sync_state "
               "(table_name VARCHAR PRIMARY KEY, strategy VARCHAR, last_value BIGINT, synced_at TIMESTAMP)")
    wh.execute("CREATE TABLE IF NOT EXISTS _sync_log (run_id VARCHAR, table_name VARCHAR, mode VARCHAR, "
               "rows_read BIGINT, bytes_read BIGINT, keys_checked BIGINT, rows_upserted BIGINT, rows_deleted BIGINT, "
               "seconds DOUBLE, finished_at TIMESTAMP)")


def last_synced_value(wh, name):
    row = wh.execute("SELECT last_value FROM _sync_state WHERE table_name = ?", [name]).fetchone()
    return row[0] if row else None


# ---------------------------------------------- extract (source) ----------------------------------------------

def extract(src, name, spec, last, full):
    """Read the rows to load from one consistent snapshot of the source.

    Returns (mode, rows, deleted_keys, new_watermark).
    """
    key = key_of(spec)
    columns = ", ".join(f"t.[{column}]" for column in spec["columns"])
    cur = src.cursor()
    cur.execute("SET TRANSACTION ISOLATION LEVEL SNAPSHOT")
    cur.execute("BEGIN TRANSACTION")
    try:
        if spec["strategy"] == "change_tracking":
            current = cur.execute("SELECT CHANGE_TRACKING_CURRENT_VERSION()").fetchval()
            min_valid = cur.execute("SELECT CHANGE_TRACKING_MIN_VALID_VERSION(OBJECT_ID(?))", f"dbo.{name}").fetchval()
            
            if full or last is None or last < min_valid:
                rows = cur.execute(f"SELECT {columns} FROM dbo.{name} t").fetchall()
                return "full", rows, [], current
            changes = cur.execute(
                f"SELECT ct.[{key}], {columns} FROM CHANGETABLE(CHANGES dbo.{name}, {int(last)}) ct "
                f"LEFT JOIN dbo.{name} t ON t.[{key}] = ct.[{key}]"
            ).fetchall()
            rows = [change[1:] for change in changes if change[1] is not None]
            deleted = [change[0] for change in changes if change[1] is None]  
            return "incremental", rows, deleted, current

        if spec["strategy"] == "rowversion":

            bound = cur.execute("SELECT CAST(MIN_ACTIVE_ROWVERSION() AS BIGINT) - 1").fetchval()
            low = 0 if (full or last is None) else last
            rows = cur.execute(
                f"SELECT {columns} FROM dbo.{name} t "
                "WHERE t.RowVer > CONVERT(binary(8), CAST(? AS bigint)) AND t.RowVer <= CONVERT(binary(8), CAST(? AS bigint))",
                low, bound,
            ).fetchall()
            return ("full" if low == 0 else "incremental"), rows, [], bound

        
        low = 0 if (full or last is None) else last
        rows = cur.execute(f"SELECT {columns} FROM dbo.{name} t WHERE t.[{key}] > ? ORDER BY t.[{key}]", low).fetchall()
        return ("full" if low == 0 else "incremental"), rows, [], (rows[-1][0] if rows else low)
    finally:
        cur.execute("IF @@TRANCOUNT > 0 COMMIT") 
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")


def find_deleted_by_range(src, wh, name, key):
   
    max_loaded = wh.execute(f"SELECT max({key}) FROM {name}").fetchone()[0]
    if max_loaded is None:
        return [], 0
    cur = src.cursor()
    source_counts = dict(cur.execute(
        f"SELECT [{key}] / {BUCKET_SIZE}, COUNT(*) FROM dbo.{name} WHERE [{key}] <= ? GROUP BY [{key}] / {BUCKET_SIZE}",
        max_loaded,
    ).fetchall())
    warehouse_counts = dict(wh.execute(
        f"SELECT {key} // {BUCKET_SIZE}, count(*) FROM {name} WHERE NOT _is_deleted GROUP BY 1"
    ).fetchall())

    deleted, keys_checked = [], 0
    for bucket, count in warehouse_counts.items():
        if count == source_counts.get(bucket, 0):
            continue
        low, high = bucket * BUCKET_SIZE, (bucket + 1) * BUCKET_SIZE
        source_keys = {row[0] for row in cur.execute(
            f"SELECT [{key}] FROM dbo.{name} WHERE [{key}] >= ? AND [{key}] < ?", low, high).fetchall()}
        warehouse_keys = [row[0] for row in wh.execute(
            f"SELECT {key} FROM {name} WHERE NOT _is_deleted AND {key} >= ? AND {key} < ?", [low, high]).fetchall()]
        keys_checked += len(source_keys)
        deleted += [k for k in warehouse_keys if k not in source_keys]
    return deleted, keys_checked


# -------------------------------------------- load (warehouse) --------------------------------------------

def write_staging(path, rows):

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(["\\N" if value is None else value for value in row])
    return path.stat().st_size


def soft_delete(wh, name, condition, now):
    return wh.execute(
        f"UPDATE {name} SET _is_deleted = true, _deleted_at = ? WHERE NOT _is_deleted AND {condition}", [now]
    ).fetchone()[0]


def load(wh, name, spec, mode, rows, deleted, new_value):
    
    key, columns = key_of(spec), list(spec["columns"])
    staging_file = STAGING_DIR / f"{name}.csv"
    bytes_read = write_staging(staging_file, rows)
    now = utc_now()
    deleted_count = 0

    wh.begin()
    try:
        if rows:
            types = ", ".join(f"'{column}': '{kind}'" for column, kind in spec["columns"].items())
            wh.execute(
                "CREATE OR REPLACE TEMP TABLE stg AS SELECT * FROM read_csv(?, header = false, delim = ',', "
                f"quote = '\"', escape = '\"', nullstr = '\\N', columns = {{{types}}})",
                [str(staging_file)],
            )
            wh.execute(
                f"MERGE INTO {name} t USING stg s ON t.{key} = s.{key} "
                f"WHEN MATCHED THEN UPDATE SET {', '.join(f'{c} = s.{c}' for c in columns)}, "
                "_is_deleted = false, _deleted_at = NULL, _loaded_at = ? "
                f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}, _is_deleted, _deleted_at, _loaded_at) "
                f"VALUES ({', '.join(f's.{c}' for c in columns)}, false, NULL, ?)",
                [now, now],
            )
        if mode == "full":
            
            condition = f"{key} NOT IN (SELECT {key} FROM stg)" if rows else "true"
            deleted_count = soft_delete(wh, name, condition, now)
        elif deleted:
            wh.execute("CREATE OR REPLACE TEMP TABLE deleted_keys AS SELECT unnest(?) AS k", [deleted])
            deleted_count = soft_delete(wh, name, f"{key} IN (SELECT k FROM deleted_keys)", now)

        wh.execute("INSERT OR REPLACE INTO _sync_state VALUES (?, ?, ?, ?)", [name, spec["strategy"], new_value, now])
        if FAIL_DURING == name:
            raise RuntimeError(f"Injected failure while loading {name} (FAIL_DURING={name})")
        wh.commit()
    except Exception:
        wh.rollback()
        raise
    finally:
        staging_file.unlink(missing_ok=True)
    return bytes_read, len(rows), deleted_count


# ---------------------------------------------- main ----------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Incremental load FundoSource -> DuckDB")
    parser.add_argument("--full", action="store_true", help="force a full reload of every table")
    args = parser.parse_args()

    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    src = pyodbc.connect(SOURCE_ODBC, autocommit=True)  # read transactions are opened explicitly in extract()
    wh = duckdb.connect(str(WAREHOUSE_PATH))
    run_id = utc_now().strftime("%Y%m%dT%H%M%S")
    print(f"Sync run {run_id} -> {WAREHOUSE_PATH}")
    print(f"  {'table':<16}{'mode':<13}{'rows read':>10}{'bytes read':>13}{'keys chk':>10}"
          f"{'upserted':>10}{'deleted':>9}{'secs':>7}")

    total_rows = total_bytes = 0
    try:
        setup_warehouse(wh)
        for name, spec in TABLES.items():
            started = time.perf_counter()
            mode, rows, deleted, new_value = extract(src, name, spec, last_synced_value(wh, name), args.full)
            keys_checked = 0
            if spec["strategy"] == "rowversion" and mode == "incremental":
                deleted, keys_checked = find_deleted_by_range(src, wh, name, key_of(spec))
            bytes_read, upserted, deleted_count = load(wh, name, spec, mode, rows, deleted, new_value)
            seconds = time.perf_counter() - started

            wh.execute("INSERT INTO _sync_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       [run_id, name, mode, len(rows), bytes_read, keys_checked, upserted, deleted_count, seconds, utc_now()])
            total_rows += len(rows)
            total_bytes += bytes_read
            print(f"  {name:<16}{mode:<13}{len(rows):>10,}{bytes_read:>13,}{keys_checked:>10,}"
                  f"{upserted:>10,}{deleted_count:>9,}{seconds:>7.1f}")
    finally:
        src.close()
        wh.close()

    print(f"  {'tmp_export_2021':<16}{'not loaded':<13}  (unused scratch table)")
    print(f"  total: {total_rows:,} rows, {total_bytes:,} bytes read from the source")


if __name__ == "__main__":
    main()
