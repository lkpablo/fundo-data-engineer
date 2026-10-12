
#  -- Import faker to ingest the data into the tables and pyodbc to connect with SQL and 

import json
import os
import random
from datetime import date, datetime, timedelta
from decimal import Decimal

import pyodbc
from faker import Faker

# -- Variables for faker to generate the data and insert the data

SEED = 42
N_CUSTOMERS = 5000
N_TRANSACTIONS = 200_000
BATCH_SIZE = 10_000
START = datetime(2019, 1, 1)
TODAY = datetime(2026, 10, 1) 

CONN_STR = os.environ.get(
    "SOURCE_ODBC",
    "DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;DATABASE=FundoSource;Trusted_Connection=yes;",
)

DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"]
ADVANCE_STATUS = {"requested": 10, "approved": 10, "funded": 35, "paid_off": 35, "cancelled": 10}
TXN_TYPES = {"purchase": 80, "refund": 5, "fee": 5, "repayment": 7, "disbursement": 3}
TXN_AMOUNT = {"purchase": (1, 300), "refund": (1, 100), "fee": (1, 15), "repayment": (20, 500), "disbursement": (50, 500)}

random.seed(SEED)
Faker.seed(SEED)
fake = Faker("en_US")


# ------------------------------------------------ General data ------------------------------------------------

def rand_dt(start, end):
    if end <= start:
        return start
    return start + timedelta(seconds=random.randint(0, int((end - start).total_seconds())))


def money(low, high):
    return Decimal(f"{random.uniform(low, high):.2f}")


def weighted(options):
    return random.choices(list(options), weights=list(options.values()))[0]


def phone():
    
    area, exchange, line = random.randint(201, 989), random.randint(200, 999), random.randint(0, 9999)
    style = random.choices([0, 1, 2, 3], weights=[50, 25, 15, 10])[0]
    return [
        f"({area}) {exchange}-{line:04d}",
        f"{area}-{exchange}-{line:04d}",
        f"+1 {area} {exchange} {line:04d}",
        f"{area}{exchange}{line:04d}",
    ][style]


def reformat_phone(value):
    """Same digits, different format: a duplicate typed by the same person."""
    digits = "".join(ch for ch in value if ch.isdigit())[-10:]
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:]}"


# ---------------------------------------------- customers ------------------------------------------------

def build_customers():
    customers = []
    used_ssn = set()

    def unique_ssn():
        while True:
            ssn = fake.ssn()
            if ssn not in used_ssn:
                used_ssn.add(ssn)
                return ssn

    def new_person(kind, created=None):
        first, last = fake.first_name(), fake.last_name()
        created = created or rand_dt(START, TODAY - timedelta(days=30))
        person = {
            "FirstName": first,
            "LastName": last,
            "Email": f"{first}.{last}{random.randint(1, 999)}@{random.choice(DOMAINS)}".lower(),
            "Phone": phone() if random.random() > 0.05 else None,
            "NationalId": unique_ssn(),
            "DateOfBirth": date(1946, 1, 1) + timedelta(days=random.randint(0, 22000)),
            "CreatedAt": created,
            "UpdatedAt": rand_dt(created, TODAY),
            "_kind": kind,
        }
        customers.append(person)
        return person

    def copy_person(original, kind, **changes):
        created = rand_dt(original["CreatedAt"], TODAY - timedelta(days=1))
        dup = {k: v for k, v in original.items() if not k.startswith("_")}
        dup.update(CreatedAt=created, UpdatedAt=rand_dt(created, TODAY), _kind=kind, **changes)
        customers.append(dup)
        return dup


    for group in range(120):
        original = new_person("dup")
        original["_group"] = group
        members = [original]
        for _ in range(2 if group >= 100 else 1):
            ssn = original["NationalId"]
            dup = copy_person(
                original, "dup",
                FirstName=random.choice([original["FirstName"], original["FirstName"].upper(), original["FirstName"] + " "]),
                Email=random.choice([original["Email"].upper(), f"{original['FirstName']}{random.randint(1, 99)}@{random.choice(DOMAINS)}".lower()]),
                Phone=reformat_phone(original["Phone"]) if original["Phone"] else phone(),
                NationalId=random.choice([ssn, ssn.replace("-", ""), ssn.replace("-", " ")]),
            )
            dup["_group"] = group
            members.append(dup)
        if group < 5:
            original["_force_advance"] = random.choice(["funded", "paid_off"])
        elif group < 10:
            members[-1]["_force_advance"] = random.choice(["funded", "paid_off"])
        elif group < 12:
            original["_force_advance"] = "funded"
            members[-1]["_force_advance"] = "paid_off"

    # 2. Look-alikes that are different people (only SUGGEST a match).
    for _ in range(15):
        a = new_person("lookalike")
        b = new_person("lookalike")
        b.update(FirstName=a["FirstName"], LastName=a["LastName"], DateOfBirth=a["DateOfBirth"])
    for _ in range(15): 
        a = new_person("lookalike")
        b = new_person("lookalike")
        b.update(LastName=a["LastName"], Email=a["Email"])
    for _ in range(10):
        a = new_person("lookalike")
        b = new_person("lookalike")
        b["Phone"] = a["Phone"] or phone()
        a["Phone"] = b["Phone"]

    # 3. Possible duplicates without NationalId
    for _ in range(10):
        a = new_person("no_id")
        a["NationalId"] = None
        copy_person(a, "no_id", NationalId=None)

    # 4. Test accounts (to exclude). Several share a placeholder NationalId,
    test_accounts = [
        ("Test", "User", "test@fundo.com"), ("Test", "User", "test1@fundo.com"),
        ("Test", "Account", "test2@fundo.com"), ("QA", "Test", "test+qa@fundo.com"),
        ("Test", "Test", "qa.test@fundo.com"), ("Test", "Customer", "dev+test@fundo.com"),
        ("Demo", "User", "demo@fundo.com"), ("Load", "Test", "loadtest01@fundo.com"),
        ("Load", "Test", "loadtest02@fundo.com"), ("John", "Doe", "test.user@example.com"),
        ("Jane", "Doe", "testing123@mailinator.com"), ("Test", "Payments", "test-payments@fundo.com"),
    ]
    for i, (first, last, email) in enumerate(test_accounts):
        person = new_person("test")
        person.update(FirstName=first, LastName=last, Email=email, Phone=f"(555) 555-01{i:02d}",
                      NationalId="000-00-0000" if i % 2 == 0 else None)

    # 5. Real people a naive "test" rule would catch.
    for first, last, email in [("Ryan", "Testerman", "ryan.testerman@gmail.com"),
                               ("Laura", "Testani", "lauratestani@yahoo.com")]:
        new_person("real_lookalike").update(FirstName=first, LastName=last, Email=email)
    for _ in range(6):
        person = new_person("staff")
        person["Email"] = f"{person['FirstName']}.{person['LastName']}@fundo.com".lower()

    # 6. Regular customers.
    while len(customers) < N_CUSTOMERS:
        new_person("regular")

    # 7. Malformed contacts on regular customers.
    regulars = [c for c in customers if c["_kind"] == "regular"]
    for c in random.sample(regulars, 60):
        first, last = c["FirstName"].lower(), c["LastName"].lower()
        c["Email"] = random.choice([
            f"{first}.{last}gmail.com", f"{first}@@gmail.com", f"{first}.{last}@",
            f"{first} {last}@gmail.com", f"{first}@gmail", "n/a",
        ])
        c["_malformed_email"] = True
    for c in random.sample(regulars, 60):
        c["Phone"] = random.choice(["555-12", "CALL-ME", "(000) 000-0000", "12345678901234", "", "n/a"])
        c["_malformed_phone"] = True

    for i, c in enumerate(customers, start=1):
        c["CustomerRef"] = f"CUS-{i:06d}"
    return customers


def insert_customers(cur, customers):
    
    cur.fast_executemany = False
    cur.executemany(
        "INSERT INTO dbo.Customers (CustomerRef, FirstName, LastName, Email, Phone, NationalId, DateOfBirth, CreatedAt, UpdatedAt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(c["CustomerRef"], c["FirstName"], c["LastName"], c["Email"], c["Phone"], c["NationalId"],
          c["DateOfBirth"], c["CreatedAt"], c["UpdatedAt"]) for c in customers],
    )
    ids = dict(cur.execute("SELECT CAST(CustomerRef AS varchar(20)), CustomerId FROM dbo.Customers").fetchall())
    for c in customers:
        c["CustomerId"] = ids[c["CustomerRef"]]


# ------------------------------------- advances, cards, transactions -------------------------------------

def make_advance(customer, status):
    requested = rand_dt(customer["CreatedAt"], TODAY)
    funded = min(requested + timedelta(days=random.randint(0, 3)), TODAY) if status in ("funded", "paid_off") else None
    paid_off = min(funded + timedelta(days=random.randint(14, 60)), TODAY) if status == "paid_off" else None
    updated = paid_off or funded or requested
    return (customer["CustomerId"], money(50, 500), status, requested, funded, paid_off, requested, updated)


def build_advances(customers):
    rows = []
    for c in customers:
        if c.get("_force_advance"):
            rows.append(make_advance(c, c["_force_advance"]))
        elif c["_kind"] == "dup":
            # Duplicates may own an open or cancelled advance: a record that must follow the merge.
            if random.random() < 0.4:
                rows.append(make_advance(c, random.choice(["requested", "cancelled"])))
        elif c["_kind"] == "test":
            if random.random() < 0.2:
                rows.append(make_advance(c, "requested"))
        elif random.random() < 0.5:
            for _ in range(random.randint(1, 2)):
                rows.append(make_advance(c, weighted(ADVANCE_STATUS)))
    return rows


def build_cards(customers):
    rows = []
    for c in customers:
        count = random.choices([0, 1, 2], weights=[25, 55, 20])[0]
        if c["_kind"] == "dup":
            count = max(count, 1)  # every duplicate owns a card, so the merge must move it
        for _ in range(count):
            exp_year, exp_month = random.randint(2024, 2030), random.randint(1, 12)
            expired = datetime(exp_year, exp_month, 1) < TODAY
            status = "expired" if expired else random.choices(["active", "blocked"], weights=[95, 5])[0]
            created = rand_dt(c["CreatedAt"], TODAY)
            rows.append((c["CustomerId"], random.choice(["Visa", "Mastercard", "Amex"]),
                         f"{random.randint(0, 9999):04d}", exp_month, exp_year, status, created, created))
    return rows


def build_transactions(customers, cards_by_customer):
    eligible = [c for c in customers if c["_kind"] != "test"]
    rows = []
    for _ in range(N_TRANSACTIONS):
        c = random.choice(eligible)
        txn_type = weighted(TXN_TYPES)
        cards = cards_by_customer.get(c["CustomerId"], [])
        card_id = random.choice(cards) if cards and txn_type in ("purchase", "refund", "fee") else None
        rows.append((c["CustomerId"], card_id, money(*TXN_AMOUNT[txn_type]), txn_type, rand_dt(c["CreatedAt"], TODAY)))
    rows.sort(key=lambda r: r[4])  # ids grow with time: mostly historical, like the real table
    return rows


# ---------------------------------------- history and scratch ----------------------------------------

def build_history(customers):
    rows = []
    candidates = [c for c in customers if c["_kind"] != "test"]
    for c in random.sample(candidates, 1500):
        for _ in range(random.randint(1, 3)):
            field = random.choice(["Email", "Phone", "LastName"])
            old = {"Email": fake.email(), "Phone": phone(), "LastName": fake.last_name()}[field]
            rows.append((c["CustomerId"], field, old, c[field], rand_dt(c["CreatedAt"], c["UpdatedAt"]),
                         random.choice(["app_service", "support_agent", "backoffice_sync"])))
    rows.sort(key=lambda r: r[4])
    return rows


def build_scratch():
    return [(i, json.dumps({"customer_ref": f"CUS-{random.randint(1, N_CUSTOMERS):06d}", "note": "manual export"}),
             rand_dt(datetime(2021, 3, 1), datetime(2021, 3, 31))) for i in range(1, 301)]


def insert_batches(cur, sql, rows):
    for i in range(0, len(rows), BATCH_SIZE):
        cur.executemany(sql, rows[i:i + BATCH_SIZE])


# ------------------------------------------------ main ------------------------------------------------

def main():
    conn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = conn.cursor()
    if cur.execute("SELECT COUNT(*) FROM dbo.Customers").fetchone()[0] > 0:
        raise SystemExit("FundoSource already has data. Recreate it with source/00 and source/01 before seeding.")

    try:
        customers = build_customers()
        insert_customers(cur, customers)

        cur.fast_executemany = True
        advances = build_advances(customers)
        insert_batches(cur, "INSERT INTO dbo.Advances (CustomerId, Amount, Status, RequestedAt, FundedAt, PaidOffAt, CreatedAt, UpdatedAt) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", advances)

        cards = build_cards(customers)
        insert_batches(cur, "INSERT INTO dbo.Cards (CustomerId, Brand, Last4, ExpMonth, ExpYear, Status, CreatedAt, UpdatedAt) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", cards)
        cards_by_customer = {}
        for card_id, customer_id in cur.execute("SELECT CardId, CustomerId FROM dbo.Cards").fetchall():
            cards_by_customer.setdefault(customer_id, []).append(card_id)

        transactions = build_transactions(customers, cards_by_customer)
        insert_batches(cur, "INSERT INTO dbo.Transactions (CustomerId, CardId, Amount, TxnType, TxnDate) "
                            "VALUES (?, ?, ?, ?, ?)", transactions)

        history = build_history(customers)
        insert_batches(cur, "INSERT INTO dbo.CustomerHistory (CustomerId, FieldName, OldValue, NewValue, ChangedAt, ChangedBy) "
                            "VALUES (?, ?, ?, ?, ?, ?)", history)

        cur.fast_executemany = False  # Payload is NVARCHAR(MAX)
        cur.executemany("INSERT INTO dbo.tmp_export_2021 (ExportId, Payload, ExportedAt) VALUES (?, ?, ?)", build_scratch())

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    kinds = {}
    for c in customers:
        kinds[c["_kind"]] = kinds.get(c["_kind"], 0) + 1
    print("Seed complete")
    print(f"  Customers        {len(customers):>8,}  {kinds}")
    print(f"  Advances         {len(advances):>8,}")
    print(f"  Cards            {len(cards):>8,}")
    print(f"  Transactions     {len(transactions):>8,}")
    print(f"  CustomerHistory  {len(history):>8,}")
    print(f"  tmp_export_2021  {300:>8,}")
    print(f"  Malformed emails {sum(1 for c in customers if c.get('_malformed_email')):>8}")
    print(f"  Malformed phones {sum(1 for c in customers if c.get('_malformed_phone')):>8}")


if __name__ == "__main__":
    main()
