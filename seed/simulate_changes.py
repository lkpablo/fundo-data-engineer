
import argparse
import random
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pyodbc
from faker import Faker

from seed import CONN_STR, DOMAINS, TODAY, money, phone  # main() of seed.py does not run on import

EMAIL_OK = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}")


def digits(value):
    return "".join(ch for ch in value if ch.isdigit()) if value else ""


def regular_customers(cur):
    rows = cur.execute(
        "SELECT CustomerId, NationalId, Email, Phone, FirstName, LastName FROM dbo.Customers ORDER BY CustomerId"
    ).fetchall()

    def identity_keys(r):
        return [("id", digits(r.NationalId)), ("email", (r.Email or "").lower()), ("phone", digits(r.Phone)),
                ("name", f"{r.FirstName.strip().lower()} {r.LastName.strip().lower()}")]

    seen = Counter(k for r in rows for k in identity_keys(r))
    return [
        r.CustomerId for r in rows
        if r.NationalId and r.Email and EMAIL_OK.fullmatch(r.Email) and len(digits(r.Phone)) in (10, 11)
        and not r.Email.lower().endswith("@fundo.com")
        and all(seen[k] == 1 for k in identity_keys(r))
    ]


def main():
    parser = argparse.ArgumentParser(description="Apply ~1% of inserts, updates and deletes to FundoSource")
    parser.add_argument("--seed", type=int, default=None, help="random seed (default: different every run)")
    args = parser.parse_args()
    random.seed(args.seed)
    Faker.seed(args.seed)
    fake = Faker("en_US")
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = conn.cursor()
    applied = Counter()
    try:
        pool = regular_customers(cur)


        max_id = cur.execute("SELECT MAX(CustomerId) FROM dbo.Customers").fetchval()
        used_ids = {digits(r[0]) for r in cur.execute("SELECT NationalId FROM dbo.Customers WHERE NationalId IS NOT NULL")}
        new_customers = []
        for i in range(20):
            ssn = fake.ssn()
            while digits(ssn) in used_ids:
                ssn = fake.ssn()
            used_ids.add(digits(ssn))
            first, last = fake.first_name(), fake.last_name()
            new_customers.append((
                f"CUS-{max_id + 1 + i:06d}", first, last,
                f"{first}.{last}{random.randint(1, 999)}@{random.choice(DOMAINS)}".lower(), phone(), ssn,
                date(1946, 1, 1) + timedelta(days=random.randint(0, 22000)), now, now,
            ))
        cur.executemany(
            "INSERT INTO dbo.Customers (CustomerRef, FirstName, LastName, Email, Phone, NationalId, DateOfBirth, CreatedAt, UpdatedAt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", new_customers)
        applied["Customers inserted"] = len(new_customers)

        for customer_id in random.sample(pool, 25):
            field = random.choice(["Email", "Phone"])
            old = cur.execute(f"SELECT {field} FROM dbo.Customers WHERE CustomerId = ?", customer_id).fetchval()
            new = f"{fake.user_name()}{random.randint(1, 99)}@{random.choice(DOMAINS)}" if field == "Email" else phone()
            cur.execute(f"UPDATE dbo.Customers SET {field} = ?, UpdatedAt = ? WHERE CustomerId = ?", new, now, customer_id)
            cur.execute("INSERT INTO dbo.CustomerHistory (CustomerId, FieldName, OldValue, NewValue, ChangedAt, ChangedBy) "
                        "VALUES (?, ?, ?, ?, ?, 'app_service')", customer_id, field, old, new, now)
            applied["Customers updated"] += 1
            applied["CustomerHistory inserted"] += 1

        closable = [r[0] for r in cur.execute(
            "SELECT c.CustomerId FROM dbo.Customers c WHERE c.CreatedAt > ? "
            "AND NOT EXISTS (SELECT 1 FROM dbo.Advances a WHERE a.CustomerId = c.CustomerId) "
            "AND NOT EXISTS (SELECT 1 FROM dbo.Cards d WHERE d.CustomerId = c.CustomerId) "
            "AND NOT EXISTS (SELECT 1 FROM dbo.Transactions t WHERE t.CustomerId = c.CustomerId) "
            "AND c.CreatedAt < ?", TODAY, now)]
        for customer_id in random.sample(closable, min(3, len(closable))):
            cur.execute("DELETE FROM dbo.Customers WHERE CustomerId = ?", customer_id)
            if customer_id in pool:
                pool.remove(customer_id)  # a closed account gets no new advances, cards or transactions
            applied["Customers deleted"] += 1

        
        for customer_id in random.sample(pool, 10):
            cur.execute("INSERT INTO dbo.Advances (CustomerId, Amount, Status, RequestedAt, CreatedAt, UpdatedAt) "
                        "VALUES (?, ?, 'requested', ?, ?, ?)", customer_id, money(50, 500), now, now, now)
            applied["Advances inserted"] += 1

        pool_set = set(pool)
        advances = [r for r in cur.execute("SELECT AdvanceId, CustomerId, Status FROM dbo.Advances WHERE CreatedAt < ? ORDER BY AdvanceId", now)
                    if r.CustomerId in pool_set]
        movable = [r for r in advances if r.Status in ("requested", "approved", "funded")]
        moved = random.sample(movable, 20)
        for advance in moved:
            if advance.Status == "requested":
                cur.execute("UPDATE dbo.Advances SET Status = 'approved', UpdatedAt = ? WHERE AdvanceId = ?", now, advance.AdvanceId)
            elif advance.Status == "approved":
                cur.execute("UPDATE dbo.Advances SET Status = 'funded', FundedAt = ?, UpdatedAt = ? WHERE AdvanceId = ?",
                            now, now, advance.AdvanceId)
            else:
                cur.execute("UPDATE dbo.Advances SET Status = 'paid_off', PaidOffAt = ?, UpdatedAt = ? WHERE AdvanceId = ?",
                            now, now, advance.AdvanceId)
            applied["Advances updated"] += 1

        moved_ids = {m.AdvanceId for m in moved}
        withdrawable = [r.AdvanceId for r in advances if r.Status in ("requested", "cancelled") and r.AdvanceId not in moved_ids]
        for advance_id in random.sample(withdrawable, min(5, len(withdrawable))):
            cur.execute("DELETE FROM dbo.Advances WHERE AdvanceId = ?", advance_id)
            applied["Advances deleted"] += 1

        for customer_id in random.sample(pool, 15):
            cur.execute("INSERT INTO dbo.Cards (CustomerId, Brand, Last4, ExpMonth, ExpYear, Status, CreatedAt, UpdatedAt) "
                        "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)", customer_id, random.choice(["Visa", "Mastercard", "Amex"]),
                        f"{random.randint(0, 9999):04d}", random.randint(1, 12), random.randint(2027, 2031), now, now)
            applied["Cards inserted"] += 1

        cards = [r for r in cur.execute("SELECT CardId, CustomerId, Status FROM dbo.Cards WHERE Status <> 'expired' ORDER BY CardId")
                 if r.CustomerId in pool_set]
        toggled = random.sample(cards, 25)
        for card in toggled:
            new_status = "active" if card.Status == "blocked" else "blocked"
            cur.execute("UPDATE dbo.Cards SET Status = ?, UpdatedAt = ? WHERE CardId = ?", new_status, now, card.CardId)
            applied["Cards updated"] += 1

        toggled_ids = {card.CardId for card in toggled}
        unused = [r.CardId for r in cur.execute(
            "SELECT c.CardId, c.CustomerId FROM dbo.Cards c WHERE c.CreatedAt < ? "
            "AND NOT EXISTS (SELECT 1 FROM dbo.Transactions t WHERE t.CardId = c.CardId) ORDER BY c.CardId", now)
            if r.CustomerId in pool_set and r.CardId not in toggled_ids]
        for card_id in random.sample(unused, min(8, len(unused))):
            cur.execute("DELETE FROM dbo.Cards WHERE CardId = ?", card_id)
            applied["Cards deleted"] += 1

        cards_by_customer = {}
        for card_id, customer_id in cur.execute("SELECT CardId, CustomerId FROM dbo.Cards WHERE Status = 'active'"):
            cards_by_customer.setdefault(customer_id, []).append(card_id)
        new_transactions = []
        for _ in range(1950):
            customer_id = random.choice(pool)
            txn_type = random.choices(["purchase", "refund", "fee", "repayment"], weights=[85, 5, 5, 5])[0]
            card_id = random.choice(cards_by_customer[customer_id]) if txn_type != "repayment" and customer_id in cards_by_customer else None
            new_transactions.append((customer_id, card_id, money(1, 300), txn_type, now - timedelta(seconds=random.randint(0, 86400))))
        cur.fast_executemany = True
        cur.executemany("INSERT INTO dbo.Transactions (CustomerId, CardId, Amount, TxnType, TxnDate) VALUES (?, ?, ?, ?, ?)",
                        new_transactions)
        cur.fast_executemany = False
        applied["Transactions inserted"] = len(new_transactions)

        max_txn = cur.execute("SELECT MAX(TransactionId) FROM dbo.Transactions").fetchval()
        for transaction_id in random.sample(range(1, max_txn - len(new_transactions)), 50):
            if applied["Transactions updated"] < 40:
                cur.execute("UPDATE dbo.Transactions SET Amount = Amount + 1 WHERE TransactionId = ?", transaction_id)
                applied["Transactions updated"] += cur.rowcount
            else:
                cur.execute("DELETE FROM dbo.Transactions WHERE TransactionId = ?", transaction_id)
                applied["Transactions deleted"] += cur.rowcount

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"Simulated changes at {now:%Y-%m-%d %H:%M:%S} UTC")
    for change, count in applied.items():
        print(f"  {change:<26}{count:>6,}")


if __name__ == "__main__":
    main()
