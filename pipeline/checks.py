
import sys

import duckdb
import pyodbc

from sync import SOURCE_ODBC, TABLES, WAREHOUSE_PATH, key_of

# Same SQL on both engines: plain aggregates that T-SQL and DuckDB compute identically.
CONTENT = {
    "Customers": "MAX(UpdatedAt), COUNT(Email), COUNT(Phone), COUNT(NationalId)",
    "Advances": "SUM(Amount), MAX(UpdatedAt), COUNT(FundedAt), COUNT(PaidOffAt)",
    "Cards": "MAX(UpdatedAt), SUM(CASE WHEN Status = 'active' THEN 1 ELSE 0 END)",
    "Transactions": "SUM(Amount), SUM(CAST(CustomerId AS BIGINT)), COUNT(CardId)",
    "CustomerHistory": "MAX(ChangedAt), SUM(CAST(CustomerId AS BIGINT))",
}


def sample(keys):
    keys = sorted(keys)
    return ", ".join(str(k) for k in keys[:5]) + (" ..." if len(keys) > 5 else "")


def check_table(src, wh, name, key):
    source_keys = {row[0] for row in src.execute(f"SELECT {key} FROM dbo.{name}").fetchall()}
    warehouse_keys = {row[0] for row in wh.execute(f"SELECT {key} FROM {name} WHERE NOT _is_deleted").fetchall()}
    missing = source_keys - warehouse_keys
    extra = warehouse_keys - source_keys

    source_content = tuple(src.execute(f"SELECT {CONTENT[name]} FROM dbo.{name}").fetchone())
    warehouse_content = tuple(wh.execute(f"SELECT {CONTENT[name]} FROM {name} WHERE NOT _is_deleted").fetchone())

    return [
        ("row count", len(source_keys) == len(warehouse_keys),
         f"source {len(source_keys):,} / warehouse {len(warehouse_keys):,}"),
        ("missing keys (gaps)", not missing, f"{len(missing):,} {sample(missing)}".strip()),
        ("extra keys (deleted at source)", not extra, f"{len(extra):,} {sample(extra)}".strip()),
        ("content", source_content == warehouse_content,
         "aggregates match" if source_content == warehouse_content
         else f"source [{', '.join(map(str, source_content))}] / warehouse [{', '.join(map(str, warehouse_content))}]"),
    ]


def main():
    src = pyodbc.connect(SOURCE_ODBC, autocommit=True).cursor()
    wh = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    failed = total = 0
    print(f"  {'table':<16}{'check':<32}{'result':<8}detail")
    try:
        for name, spec in TABLES.items():
            for check, passed, detail in check_table(src, wh, name, key_of(spec)):
                total += 1
                failed += not passed
                print(f"  {name:<16}{check:<32}{'PASS' if passed else 'FAIL':<8}{detail}")
    finally:
        src.connection.close()
        wh.close()

    print(f"\nRESULT: {'PASS' if not failed else 'FAIL'} ({total - failed} of {total} checks passed)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
