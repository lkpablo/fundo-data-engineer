# Solution

All three problems are solved in working code, in a small form that I decided. 

The part marked with **[measured]** come from runs of this repository; and the ones market with **[estimated]** are reasoned.

## 1. Move only what changed

One strategy per table, chosen by how the table changes:


    * Tables: Customers, Advances, Cards: 
    
        - Strategy: SQL Server **Change Tracking** 
    
        - Reason: Mutable and deletes matter. Change Tracking returns inserts, updates and deletes by primary key, with no SQL Agent and no full change history. 
    
        - Result: Returns only the next change, and only within its retention I chose 7 days. If the pipeline falls behind, it detects it and reloads that table in full.

    * Table: Transactions 

        - Strategy: `RowVer` watermark + delete detection by id range
        
        - Reason: The large table, mostly inserts. A watermark adds no overhead to writes on the busiest table.
        
        - Result A watermark never sees deletes. They are found by comparing row counts per range of 1,000 ids and reading keys only where counts differ.

    * Table: CustomerHistory
    
        - Strategy: `HistoryId` watermark 
    
        - Reason: Append-only.
    
        - Result: An id committed out of order could be skipped; acceptable for an audit table, `rowversion` would close it.

    * Table: tmp_export_2021 
    
    - Strategy: **Not loaded**
    
    - Reason: Unused scratch table with no key and no owner.
    
    - Result: If someone needs it, a one-time export.

How the requirements are met:

- **Initial, then incremental:** no saved state means a full load; after that, only changes.

- **Deletes:** applied as soft deletes (`_is_deleted`), so nothing in the warehouse is lost.

- **Run twice, same result:** changes are applied with `MERGE` by key. A second run with no changes reads 0 rows **[measured]**.

- **Failure mid-run:** each table and its new watermark are committed in one warehouse transaction. A failure injected while loading Cards rolled back only Cards, and the retry continued from there **[measured]**.

- **Consistent reads:** the source is read under snapshot isolation, and the `RowVer` upper bound is `MIN_ACTIVE_ROWVERSION() - 1`, so a transaction still open at read time is never skipped.

Measurements **[measured]**:

Actual problem
- Full copy (today's approach): Reads 216,451 rows and 12,061,471 Bytes

With the solution
- Incremental after ~1% of changes: Reads 2,122 rows and 117,530 (1%) Bytes.

Decisions changed by measuring:

- Loading DuckDB row by row took 281 s for 20,000 rows; writing a CSV file and bulk loading it took 1 s for 200,000. The pipeline stages a file, the same pattern as a BigQuery load job.

- Delete detection with ranges of 10,000 ids read 69,990 keys to find 10 deletes; with 1,000 it reads 9,989.

The bad schema choice is `Customers.CustomerRef NVARCHAR(MAX)`: an identifier that cannot be indexed, so its uniqueness cannot be enforced. The warehouse stores it as `VARCHAR`; the source fix is a bounded type and a unique index.

## 2. Resolve duplicate customers

`pipeline/dedup.py` builds `customer_merge` in the warehouse: one row per customer with its status (`test_excluded`, `unique`, `survivor`, `merged`, `conflict`) and its survivor. Nothing is deleted; advances, cards and transactions follow the survivor through a join.

**Identity rules**

Proves identity (merged automatically). NationalId, normalized to digits, 9 long, not a placeholder

Only suggests it (never merged). Email (shared by families), phone (shared or recycled), name + date of birth (namesakes)

A government id is issued to one person. The other fields are shared by different people, and merging on them joins people who are not the same. Suggested matches are counted for review: 25 by email, 19 by phone, 25 by name + date of birth **[measured]**.

**Business rules**

- **Funded or paid-off customers are untouchable.** In each group the customer with a funded or paid-off advance survives; otherwise the oldest. 118 groups merged, 0 funded customers merged away **[measured]**.

- **Two funded customers in one group** (2 groups): not merged. Operations should check the identity documents. If it is the same person, the business decides how both advances are kept under one account; if not, one NationalId is wrong and must be corrected at the source.

- **Test data** is excluded before grouping, with its reason: the word `test`, `qa` or `demo` as a whole word in the email, disposable domains, the name "Test", placeholder ids. Exclusion must come first: 6 test accounts share `000-00-0000` and would otherwise merge as one person. Substrings and the company domain are not enough: "Testerman", "Testani" and 6 staff with `@fundo.com` addresses stay as customers. 12 excluded **[measured]**.

- **Malformed contacts:** 60 emails and 60 phones **[measured]**. Flagged, not fixed: correcting them means guessing, and only the customer knows the right value. They are never used to match.

- **Cards** move to the survivor (162 cards **[measured]**), and the original customer stays recorded. If they stayed on the merged customer, payments could not be matched to the surviving account; if they moved to the wrong person, charges and personal data would land on a stranger's account. That is why only a proven identity merges.

**Stopping new duplicates:** a trigger on the source rejects a customer whose NationalId already exists, and the script proves it with an insert that is rolled back. It is a trigger, not a unique index, because the source already holds duplicates and the index cannot be created until they are merged there.

## 3. Prove the data is correct

`pipeline/checks.py` compares every table between source and warehouse: row count, missing keys (gaps), keys still active in the warehouse but deleted at the source, and content aggregates (amounts, last update, filled columns). It prints PASS/FAIL per check and exits with 1 on failure, so a scheduler can alert on it.

`pipeline/break_warehouse.py` damages the warehouse on purpose. With three scenarios applied, 13 of 20 checks passed, and exactly the expected ones failed; `sync.py --full` restored 20 of 20 **[measured]**. An incremental sync does not repair a damaged warehouse, because the source did not change: only the checks detect it.

## Cost impact

Today [estimated]: every day the whole database is read on the production SQL Server, sent over the network and loaded again. Cost grows with the size of the database, not with what changed.

With this pipeline: reads and transfer drop to the ~1% that changed (I measured locally: ~1% of rows and bytes). On BigQuery, a `MERGE` is billed by the bytes it scans in the target table **[estimated]**: partitioning Transactions by date and clustering by id keeps that bounded. The checks add a key-only read per table.

## Skipped, and why

- A review screen for suggested matches and conflicts: they are counted; the review is a business process.

- Applying the merge to the source: it changes the operational database and needs the application team and a backup.

- Freshness, duplicate-key and referential-integrity checks: useful, but not required. `MERGE` already keeps keys unique.

- Content checks use aggregates, so a change that keeps every sum equal would pass. A production version would compare row hashes per range.

## Towards production

Tools

- **Replication:** Google Datastream (managed CDC from SQL Server to BigQuery) if they prefer less code; or this job on Cloud Run Jobs, writing Parquet to Cloud Storage, then a load job and a `MERGE`.

- **Dedup and checks:** dbt models and tests in BigQuery, plus a reconciliation job against the source that alerts on failure.

- **Orchestration:** Cloud Scheduler is enough for one job; Airflow (Composer) only when there are more steps.

**One-time vs permanent**

- **One-time:** the initial full load; the merge applied to the source after the conflicts are reviewed, with a backup; then a unique index replacing the trigger; dropping the scratch table once nobody claims it; fixing `CustomerRef`; a retention policy for history kept forever.

- **Permanent:** the incremental load; the checks after every load; duplicate prevention at the source; the dedup rules for new customers.

**Deliver first:** the incremental load together with the checks. It cuts the daily transfer at once, and the checks are what make it safe to switch off the full copy: you cannot stop copying everything until you can prove the increments are complete. Dedup comes next, because its rules and conflicts need business sign-off.
