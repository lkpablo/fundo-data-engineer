
import duckdb
import pyodbc

from sync import SOURCE_ODBC, WAREHOUSE_PATH

RESOLVE = r"""
CREATE OR REPLACE TABLE customer_merge AS
WITH base AS (
    SELECT
        c.CustomerId, c.CreatedAt, c.Email, c.Phone,
        regexp_replace(coalesce(c.NationalId, ''), '[^0-9]', '', 'g') AS national_id,
        lower(trim(c.Email)) AS email_norm,
        regexp_replace(coalesce(c.Phone, ''), '[^0-9]', '', 'g') AS phone_digits,
        lower(trim(c.FirstName)) || ' ' || lower(trim(c.LastName)) || ' ' || coalesce(CAST(c.DateOfBirth AS VARCHAR), '') AS name_dob,
        CASE
            WHEN regexp_matches(split_part(lower(c.Email), '@', 1), '(^|[._+-])(test|testing|loadtest|qa|demo)[0-9]*([._+-]|$)')
                THEN 'test word in email'
            WHEN split_part(lower(c.Email), '@', 2) IN ('example.com', 'mailinator.com') THEN 'disposable email domain'
            WHEN lower(trim(c.FirstName)) = 'test' OR lower(trim(c.LastName)) = 'test' THEN 'test name'
            WHEN regexp_replace(coalesce(c.NationalId, ''), '[^0-9]', '', 'g') = '000000000' THEN 'placeholder NationalId'
        END AS test_reason,
        EXISTS (SELECT 1 FROM Advances a WHERE a.CustomerId = c.CustomerId AND NOT a._is_deleted
                AND a.Status IN ('funded', 'paid_off')) AS has_funded
    FROM Customers c
    WHERE NOT c._is_deleted
),
flagged AS (
    SELECT *,
        Email IS NOT NULL AND NOT regexp_full_match(email_norm, '[^@\s]+@[^@\s]+\.[a-z]{2,}') AS email_malformed,
        Phone IS NOT NULL AND (phone_digits = '0000000000' OR NOT (length(phone_digits) = 10 OR (length(phone_digits) = 11 AND phone_digits LIKE '1%'))) AS phone_malformed,
        CASE WHEN test_reason IS NULL AND length(national_id) = 9 AND national_id <> '000000000' THEN national_id END AS identity_key
    FROM base
),
grouped AS (
    SELECT *,
        count(*) OVER (PARTITION BY identity_key) AS group_size,
        count(*) FILTER (WHERE has_funded) OVER (PARTITION BY identity_key) AS funded_in_group,
        first_value(CustomerId) OVER (PARTITION BY identity_key ORDER BY has_funded DESC, CreatedAt, CustomerId) AS first_in_group
    FROM flagged
)
SELECT
    CustomerId,
    CASE
        WHEN test_reason IS NOT NULL THEN 'test_excluded'
        WHEN identity_key IS NULL OR group_size = 1 THEN 'unique'
        WHEN funded_in_group > 1 THEN 'conflict'
        WHEN CustomerId = first_in_group THEN 'survivor'
        ELSE 'merged'
    END AS status,
    CASE
        WHEN test_reason IS NOT NULL THEN NULL
        WHEN identity_key IS NULL OR group_size = 1 THEN CustomerId
        WHEN funded_in_group > 1 THEN NULL
        ELSE first_in_group
    END AS survivor_id,
    test_reason, identity_key, has_funded, email_malformed, phone_malformed, email_norm, phone_digits, name_dob
FROM grouped
"""

# Only fires when NationalId is inserted or changed, so existing duplicates can still be
PREVENT = """
CREATE OR ALTER TRIGGER dbo.trg_Customers_NoDuplicateNationalId ON dbo.Customers
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (
        SELECT 1
        FROM inserted i
        LEFT JOIN deleted d ON d.CustomerId = i.CustomerId
        JOIN dbo.Customers c
          ON c.CustomerId <> i.CustomerId
         AND REPLACE(REPLACE(c.NationalId, '-', ''), ' ', '') = REPLACE(REPLACE(i.NationalId, '-', ''), ' ', '')
        WHERE REPLACE(REPLACE(i.NationalId, '-', ''), ' ', '') <> '000000000'
          AND (d.CustomerId IS NULL OR ISNULL(d.NationalId, '') <> ISNULL(i.NationalId, ''))
    )
        THROW 50001, 'A customer with this NationalId already exists.', 1;
END
"""


def line(label, value):
    print(f"  {label:<55}{value:>7,}")


def summary(wh):
    def one(sql):
        return wh.execute(sql).fetchone()[0]

    print("Customer resolution -> warehouse table customer_merge")
    for reason, count in wh.execute("SELECT test_reason, count(*) FROM customer_merge "
                                    "WHERE test_reason IS NOT NULL GROUP BY 1 ORDER BY 2 DESC").fetchall():
        line(f"test account excluded: {reason}", count)
    line("malformed emails (flagged, not fixed)", one("SELECT count(*) FROM customer_merge WHERE email_malformed"))
    line("malformed phones (flagged, not fixed)", one("SELECT count(*) FROM customer_merge WHERE phone_malformed"))

    line("NationalId groups merged into their survivor",
         one("SELECT count(DISTINCT identity_key) FROM customer_merge WHERE status = 'merged'"))
    line("customers merged", one("SELECT count(*) FROM customer_merge WHERE status = 'merged'"))
    line("NationalId groups in conflict (2+ funded, not merged)",
         one("SELECT count(DISTINCT identity_key) FROM customer_merge WHERE status = 'conflict'"))
    line("funded customers merged away (must be 0)",
         one("SELECT count(*) FROM customer_merge WHERE status = 'merged' AND has_funded"))
    for table in ("Advances", "Cards", "Transactions"):
        line(f"{table.lower()} that now belong to the survivor",
             one(f"SELECT count(*) FROM {table} t JOIN customer_merge m ON m.CustomerId = t.CustomerId "
                 "WHERE m.status = 'merged' AND NOT t._is_deleted"))

    print("  possible matches NOT merged (they only suggest identity), in groups:")
    for label, column, valid in (("same email", "email_norm", "NOT email_malformed"),
                                 ("same phone", "phone_digits", "NOT phone_malformed AND phone_digits <> ''"),
                                 ("same name + date of birth", "name_dob", "true")):
        line(f"  {label}", one(
            f"SELECT count(*) FROM (SELECT {column} FROM customer_merge WHERE status <> 'test_excluded' AND {valid} "
            f"GROUP BY {column} HAVING count(DISTINCT coalesce(identity_key, 'none-' || CustomerId)) > 1)"))


def prevent_duplicates():
    
    conn = pyodbc.connect(SOURCE_ODBC, autocommit=True)
    cur = conn.cursor()
    cur.execute(PREVENT)
    existing = cur.execute("SELECT TOP 1 NationalId FROM dbo.Customers WHERE NationalId LIKE '[0-9][0-9][0-9]-%' "
                           "ORDER BY CustomerId").fetchval()
    conn.autocommit = False
    try:
        cur.execute("INSERT INTO dbo.Customers (CustomerRef, FirstName, LastName, NationalId, CreatedAt, UpdatedAt) "
                    "VALUES ('CUS-DUPTEST', 'Dup', 'Test', ?, GETUTCDATE(), GETUTCDATE())", existing.replace("-", ""))
        result = "NOT blocked"
    except pyodbc.Error:
        result = "blocked"
    finally:
        conn.rollback()
        conn.close()
    print("Duplicate prevention -> source trigger trg_Customers_NoDuplicateNationalId")
    print(f"  {'new customer with an existing NationalId (' + existing.replace('-', '') + ')':<55}{result:>11}")


def main():
    wh = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        wh.execute(RESOLVE)
        summary(wh)
    finally:
        wh.close()
    prevent_duplicates()


if __name__ == "__main__":
    main()
