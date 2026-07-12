"""
data/generate.py
================
Generates synthetic raw data and loads it into DuckDB/MotherDuck.

Simulates three upstream warehouse tables:
  - raw_users            10,000 user profiles
  - raw_transactions     ~1.2M transaction events (90 days of history)
  - raw_support_tickets  ~80K support ticket events

Usage:
    python data/generate.py
    python data/generate.py --users 5000 --days 60
"""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import structlog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feature_store.connections import get_duckdb_client
from feature_store.schema import apply_schema

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

PLAN_TYPES = ["free", "basic", "pro", "enterprise"]
PLAN_WEIGHTS = [0.45, 0.30, 0.18, 0.07]
COUNTRIES = ["US", "UK", "DE", "FR", "CA", "AU", "IN", "BR", "SG", "JP"]
COUNTRY_WEIGHTS = [0.35, 0.12, 0.10, 0.08, 0.08, 0.06, 0.08, 0.05, 0.04, 0.04]
AGE_BUCKETS = ["18-24", "25-34", "35-44", "45+"]
AGE_WEIGHTS = [0.20, 0.35, 0.28, 0.17]
CATEGORIES = ["subscription", "one-time", "addon", "refund-eligible", "trial"]
SEVERITIES = ["low", "medium", "high", "critical"]
SEVERITY_WEIGHTS = [0.50, 0.30, 0.15, 0.05]


def generate_users(n: int, days: int) -> list[dict]:
    log.info("generating_users", count=n)
    now = datetime.utcnow()
    users = []
    for uid in range(1, n + 1):
        signup_days_ago = random.randint(1, days * 3)  # some users older than window
        users.append(
            {
                "user_id": uid,
                "signup_date": (now - timedelta(days=signup_days_ago)).date(),
                "country": random.choices(COUNTRIES, COUNTRY_WEIGHTS)[0],
                "plan_type": random.choices(PLAN_TYPES, PLAN_WEIGHTS)[0],
                "age_bucket": random.choices(AGE_BUCKETS, AGE_WEIGHTS)[0],
            }
        )
    return users


def generate_transactions(users: list[dict], days: int) -> list[dict]:
    """
    Realistic transaction distribution:
    - Enterprise/Pro users transact more frequently and with higher amounts
    - ~5% of transactions fail (risk feature signal)
    - ~2% are refunded
    """
    log.info("generating_transactions", users=len(users), days=days)
    now = datetime.utcnow()
    txns = []
    tid = 1

    plan_freq = {"free": 1.5, "basic": 4, "pro": 8, "enterprise": 15}
    plan_amount_mu = {"free": 12, "basic": 35, "pro": 75, "enterprise": 180}

    for user in users:
        freq = plan_freq[user["plan_type"]]
        mu = plan_amount_mu[user["plan_type"]]
        n_txns = np.random.poisson(freq * days / 30)

        for _ in range(n_txns):
            days_ago = random.uniform(0, days)
            event_time = now - timedelta(days=days_ago)

            r = random.random()
            if r < 0.05:
                status = "failed"
            elif r < 0.07:
                status = "refunded"
            else:
                status = "success"

            amount = max(0.5, np.random.lognormal(mean=np.log(mu), sigma=0.6))

            txns.append(
                {
                    "transaction_id": tid,
                    "user_id": user["user_id"],
                    "amount": round(float(amount), 2),
                    "category": random.choice(CATEGORIES),
                    "status": status,
                    "event_time": event_time,
                }
            )
            tid += 1

    random.shuffle(txns)
    log.info("transactions_generated", count=len(txns))
    return txns


def generate_support_tickets(users: list[dict], days: int) -> list[dict]:
    """
    Support tickets: higher-tier users open fewer (better self-service),
    but when they do, severity is higher (more to lose).
    """
    log.info("generating_support_tickets", users=len(users), days=days)
    now = datetime.utcnow()
    tickets = []
    tid = 1

    plan_ticket_rate = {"free": 0.8, "basic": 0.5, "pro": 0.3, "enterprise": 0.4}

    for user in users:
        rate = plan_ticket_rate[user["plan_type"]]
        n_tickets = np.random.poisson(rate * days / 30)

        for _ in range(n_tickets):
            days_ago = random.uniform(0, days)
            event_time = now - timedelta(days=days_ago)
            age_days = (now - event_time).days
            # Tickets older than 14 days are likely resolved
            resolved = 1 if (age_days > 14 or random.random() < 0.75) else 0

            tickets.append(
                {
                    "ticket_id": tid,
                    "user_id": user["user_id"],
                    "severity": random.choices(SEVERITIES, SEVERITY_WEIGHTS)[0],
                    "resolved": resolved,
                    "event_time": event_time,
                }
            )
            tid += 1

    log.info("tickets_generated", count=len(tickets))
    return tickets


# ---------------------------------------------------------------------------
# DuckDB loaders — bulk insert via registered DataFrames
# ---------------------------------------------------------------------------


def load_users(client, users: list[dict]) -> None:
    log.info("loading_users_to_duckdb", count=len(users))
    client.register("u_df", pd.DataFrame(users))
    client.execute(
        "INSERT INTO raw_users (user_id, signup_date, country, plan_type, age_bucket) "
        "SELECT user_id, signup_date, country, plan_type, age_bucket FROM u_df"
    )
    log.info("users_loaded")


def load_transactions(client, txns: list[dict]) -> None:
    log.info("loading_transactions_to_duckdb", count=len(txns))
    client.register("t_df", pd.DataFrame(txns))
    client.execute(
        "INSERT INTO raw_transactions "
        "(transaction_id, user_id, amount, category, status, event_time) "
        "SELECT transaction_id, user_id, amount, category, status, event_time FROM t_df"
    )
    log.info("transactions_loaded")


def load_tickets(client, tickets: list[dict]) -> None:
    log.info("loading_tickets_to_duckdb", count=len(tickets))
    client.register("s_df", pd.DataFrame(tickets))
    client.execute(
        "INSERT INTO raw_support_tickets "
        "(ticket_id, user_id, severity, resolved, event_time) "
        "SELECT ticket_id, user_id, severity, resolved, event_time FROM s_df"
    )
    log.info("tickets_loaded")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic raw data")
    parser.add_argument("--users", type=int, default=10_000)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    log.info("connecting_to_duckdb")
    client = get_duckdb_client()
    apply_schema(client)

    # Clear existing data for clean re-runs
    for table in ["raw_users", "raw_transactions", "raw_support_tickets"]:
        client.execute(f"DELETE FROM {table}")
    log.info("tables_cleared")

    users = generate_users(args.users, args.days)
    txns = generate_transactions(users, args.days)
    tickets = generate_support_tickets(users, args.days)

    load_users(client, users)
    load_transactions(client, txns)
    load_tickets(client, tickets)

    # Verify counts
    for table in ["raw_users", "raw_transactions", "raw_support_tickets"]:
        (count,) = client.execute(f"SELECT count(*) FROM {table}")[0]
        log.info("table_loaded", table=table, rows=count)

    log.info("data_generation_complete")


if __name__ == "__main__":
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    main()
