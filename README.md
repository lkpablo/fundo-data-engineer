# Fundo take-home — Data Engineer

A local version of the SQL Server → warehouse pipeline: an incremental load that moves only what changed, checks that prove the warehouse matches the source, and duplicate customer resolution. DuckDB stands in for BigQuery.

The rest of the explanation like decisions, measurements and trade-offs are in SOLUTION.md.

## Requirements

- Docker Desktop (or Docker Engine with Compose v2), with at least 2 GB of memory available for SQL Server.
- Apple Silicon: the SQL Server image is amd64 only. Enable *Use Rosetta for x86/amd64 emulation* in Docker Desktop settings.

Run every command from the repository root. The first run downloads the images and takes a few minutes.

## 1. Start

```bash
docker compose up -d
docker compose run --rm pipeline
```

- `up -d` starts SQL Server and creates the source database (`source/00_create_database.sql`, `source/01_schema.sql`).
- `run --rm pipeline` fills the source with synthetic data (`seed/seed.py`) and runs the initial load into `data/warehouse.duckdb` (`pipeline/sync.py`).

Expected output (ends with):

```
  table           mode          rows read   bytes read  keys chk  upserted  deleted   secs
  Customers       full              5,000      676,295         0     5,000        0    0.3
  Advances        full              3,666      400,435         0     3,666        0    0.2
  Cards           full              4,836      373,525         0     4,836        0    0.2
  Transactions    full            200,000   10,368,502         0   200,000        0    3.5
  CustomerHistory full              2,949      242,714         0     2,949        0    0.2
  tmp_export_2021 not loaded     (unused scratch table)
  total: 216,451 rows, 12,061,471 bytes read from the source
```

The seed is deterministic, so these numbers are the same on every machine.

## 2. Incremental load

```bash
docker compose run --rm pipeline python seed/simulate_changes.py
docker compose run --rm pipeline python pipeline/sync.py
```

- `simulate_changes.py` applies one day of activity to the source: about 1% inserts, updates and deletes.
- `sync.py` loads only those changes. Expected: around 2,100 rows read, mode `incremental`, and deletes in the `deleted` column.

Run `sync.py` again without new changes: it reads 0 rows and the warehouse stays the same. It should works fine

## 3. Recovery from a failure mid-run

```bash
docker compose run --rm pipeline python seed/simulate_changes.py
docker compose run --rm -e FAIL_DURING=Cards pipeline python pipeline/sync.py
docker compose run --rm pipeline python pipeline/sync.py
```

- The second command fails on purpose while loading `Cards` (`RuntimeError: Injected failure ...`). `Customers` and `Advances` are already committed; `Cards` is rolled back completely.
- The retry reads 0 rows for `Customers` and `Advances` and continues from `Cards`. Nothing is lost or loaded twice.

## 4. Checks

```bash
docker compose run --rm pipeline python pipeline/checks.py
```

For every table it compares the source with the warehouse: row count, keys missing in the warehouse (gaps), keys still active in the warehouse but deleted at the source, and content aggregates. Expected:

```
RESULT: PASS (20 of 20 checks passed)
```

The command exits with code 0 when everything passes and 1 otherwise.

## 5. Failing-check demo

```bash
docker compose run --rm pipeline python pipeline/break_warehouse.py delete-rows
docker compose run --rm pipeline python pipeline/checks.py
docker compose run --rm pipeline python pipeline/sync.py --full
docker compose run --rm pipeline python pipeline/checks.py
```

1. `break_warehouse.py` damages the warehouse (never the source).
2. `checks.py` fails and names the table, the check and the keys.
3. `sync.py --full` reloads every table from the source.
4. `checks.py` passes again.

| Scenario | What it does | Checks that fail |
|---|---|---|
| `delete-rows` | removes 5 transactions | Transactions: row count, missing keys, content |
| `undelete` | revives 3 deleted cards | Cards: row count, extra keys, content |
| `corrupt-amount` | adds 100 to one advance | Advances: content |

## 6. Duplicate customers

```bash
docker compose run --rm pipeline python pipeline/dedup.py
```

- Builds the warehouse table `customer_merge`: every customer with its status (`test_excluded`, `unique`, `survivor`, `merged`, `conflict`), its survivor and its malformed email/phone flags. Nothing is deleted.
- Installs a trigger on the source that rejects a new customer whose NationalId already exists, then proves it with an insert that is rolled back.

Expected output:

```
Customer resolution -> warehouse table customer_merge
 
  test account excluded: test word in email                   12
  malformed emails (flagged, not fixed)                       60
  malformed phones (flagged, not fixed)                       60
  NationalId groups merged into their survivor               118
  customers merged                                           138
  NationalId groups in conflict (2+ funded, not merged)        2
  funded customers merged away (must be 0)                     0
  advances that now belong to the survivor                    56
  cards that now belong to the survivor                      162
  transactions that now belong to the survivor             5,531

  possible matches NOT merged (they only suggest identity), in groups:
  
    same email                                                25
    same phone                                                19
    same name + date of birth                                 25

Duplicate prevention -> source trigger trg_Customers_NoDuplicateNationalId

  new customer with an existing NationalId (282323658)       blocked
```

The transactions count changes slightly after running `simulate_changes.py`

## Inspect the data (optional)

- **Source:** any SQL Server client at `localhost,14333`, user `sa`, password `Fundo_Local_2026!`.
- **Warehouse:** open `data/warehouse.duckdb` with DBeaver or the DuckDB CLI. `_sync_state` holds the watermark of each table, `_sync_log` every run and `customer_merge` the duplicate resolution. Close the connection before running the pipeline: DuckDB allows one writer at a time.

## Stop and start over

```bash
docker compose down
```

This removes the SQL Server container and its data. Before starting again, also delete the `data/` folder: the warehouse keeps the versions of the old source.

## Layout

```
source/     SQL Server schema (database, tables, Change Tracking)
seed/       synthetic data (seed.py) and daily changes (simulate_changes.py)
pipeline/   incremental load (sync.py), checks (checks.py), break demo (break_warehouse.py),
            duplicate customers (dedup.py)
```
