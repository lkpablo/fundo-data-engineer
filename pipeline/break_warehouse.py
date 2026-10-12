
import argparse

import duckdb

from sync import WAREHOUSE_PATH

SCENARIOS = {
    "delete-rows": (
        "DELETE FROM Transactions WHERE TransactionId IN "
        "(SELECT TransactionId FROM Transactions WHERE NOT _is_deleted ORDER BY TransactionId LIMIT 5) "
        "RETURNING TransactionId"
    ),
    "undelete": (
        "UPDATE Cards SET _is_deleted = false, _deleted_at = NULL WHERE CardId IN "
        "(SELECT CardId FROM Cards WHERE _is_deleted ORDER BY CardId LIMIT 3) "
        "RETURNING CardId"
    ),
    "corrupt-amount": (
        "UPDATE Advances SET Amount = Amount + 100 WHERE AdvanceId = "
        "(SELECT min(AdvanceId) FROM Advances WHERE NOT _is_deleted) "
        "RETURNING AdvanceId"
    ),
}


def main():
    parser = argparse.ArgumentParser(description="Break the warehouse on purpose")
    parser.add_argument("scenario", choices=SCENARIOS)
    args = parser.parse_args()

    wh = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        keys = [row[0] for row in wh.execute(SCENARIOS[args.scenario]).fetchall()]
    finally:
        wh.close()
    print(f"{args.scenario}: changed keys {keys}" if keys else f"{args.scenario}: nothing to change")
    print("Run `python pipeline/checks.py` to see it fail, then `python pipeline/sync.py --full` to repair.")


if __name__ == "__main__":
    main()
