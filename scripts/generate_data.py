"""
Synthetic data generator for the One-Click Smart Upsells A/B test.

Generates four CSVs matching the project schema:
    users.csv
    experiment_assignments.csv
    checkout_sessions.csv
    orders.csv

Design notes:
- Variant is assigned ONCE per user, at their first checkout session inside
  the experiment window, and held fixed for all subsequent sessions.
- Users may also have checkout sessions BEFORE the experiment window
  (purchase history), which is what lets "frequent vs. occasional shopper"
  be a real, derivable segment downstream rather than a stored column.
- Effect sizes (AOV lift, UPT lift, abandonment delta, segment interactions)
  are intentionally embedded as constants below and are not surfaced in any
  printed output. They should be discovered through the SRM check, t-tests,
  and segment analysis -- not read off this script.
"""

import numpy as np
import pandas as pd
import string
from datetime import timedelta

# ---------------------------------------------------------------------------
# Global config
# ---------------------------------------------------------------------------

MASTER_SEED = 42

N_USERS = 20_000
SHOPPER_FRACTION = 0.65          # fraction of users who ever check out at all

SIGNUP_START = pd.Timestamp("2023-01-01")
EXPERIMENT_WINDOW_START = pd.Timestamp("2026-01-01")
EXPERIMENT_WINDOW_END = pd.Timestamp("2026-03-31")
LOOKBACK_DAYS = 90                # pre-window history window for segmentation

ID_ALPHABET = string.ascii_lowercase + string.digits


def make_id(prefix, rng, existing_ids):
    """Generate a unique prefixed alphanumeric ID."""
    while True:
        suffix = "".join(rng.choice(list(ID_ALPHABET), size=8))
        candidate = f"{prefix}_{suffix}"
        if candidate not in existing_ids:
            existing_ids.add(candidate)
            return candidate


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------

def generate_users(n_users, seed):
    rng = np.random.default_rng(seed)
    id_pool = set()

    user_ids = [make_id("usr", rng, id_pool) for _ in range(n_users)]

    signup_range_days = (EXPERIMENT_WINDOW_START - SIGNUP_START).days
    signup_offsets = rng.integers(0, signup_range_days, size=n_users)
    signup_dates = [SIGNUP_START + timedelta(days=int(d)) for d in signup_offsets]

    platforms = rng.choice(["iOS", "Android"], size=n_users, p=[0.55, 0.45])

    users_df = pd.DataFrame({
        "user_id": user_ids,
        "signup_date": [d.date().isoformat() for d in signup_dates],
        "platform": platforms,
    })
    return users_df


# ---------------------------------------------------------------------------
# experiment_assignments + checkout_sessions (coupled)
# ---------------------------------------------------------------------------

def generate_assignments_and_sessions(users_df, seed):
    rng = np.random.default_rng(seed)
    id_pool = set()

    session_rows = []
    assignment_rows = []

    signup_lookup = pd.to_datetime(users_df["signup_date"])
    lookback_start = EXPERIMENT_WINDOW_START - timedelta(days=LOOKBACK_DAYS)

    is_shopper = rng.random(len(users_df)) < SHOPPER_FRACTION

    # Activity level per shopper drives both pre-window and in-window session counts.
    # low / medium / high determines the Poisson rate for number of sessions.
    activity_level = rng.choice(
        ["low", "medium", "high"], size=len(users_df), p=[0.5, 0.35, 0.15]
    )
    activity_rate_map = {"low": 0.8, "medium": 2.2, "high": 4.5}

    for i, row in users_df.iterrows():
        if not is_shopper[i]:
            continue

        user_id = row["user_id"]
        signup_date = signup_lookup.iloc[i]
        rate = activity_rate_map[activity_level[i]]

        # --- pre-window sessions (purchase history, no variant exposure) ---
        pre_window_earliest = max(signup_date, lookback_start)
        pre_window_days_available = (EXPERIMENT_WINDOW_START - pre_window_earliest).days

        n_pre_sessions = 0
        if pre_window_days_available > 0:
            n_pre_sessions = rng.poisson(rate * (pre_window_days_available / 90))

        for _ in range(n_pre_sessions):
            offset = rng.integers(0, max(pre_window_days_available, 1))
            ts = pre_window_earliest + timedelta(
                days=int(offset), hours=int(rng.integers(0, 24))
            )
            session_rows.append({
                "session_id": make_id("ses", rng, id_pool),
                "user_id": user_id,
                "session_ts": ts,
                "completed": rng.random() < 0.78,  # baseline completion, no variant effect pre-launch
                "phase": "pre",
            })

        # --- in-window sessions (at least 1, since they must check out to enter the experiment) ---
        window_days = (EXPERIMENT_WINDOW_END - EXPERIMENT_WINDOW_START).days
        n_in_window_sessions = max(1, rng.poisson(rate * (window_days / 90)))

        in_window_offsets = sorted(rng.integers(0, window_days, size=n_in_window_sessions))
        in_window_ts = [
            EXPERIMENT_WINDOW_START + timedelta(days=int(o), hours=int(rng.integers(0, 24)))
            for o in in_window_offsets
        ]

        # Assignment happens at the first in-window session.
        first_ts = in_window_ts[0]
        variant = rng.choice(["control", "treatment"])  # 50/50 per user

        assignment_rows.append({
            "assignment_id": make_id("asn", rng, id_pool),
            "user_id": user_id,
            "variant": variant,
            "first_assigned_ts": first_ts,
        })

        # Completion probability differs slightly by variant (guardrail effect).
        completion_p = 0.78 if variant == "control" else 0.75

        for ts in in_window_ts:
            session_rows.append({
                "session_id": make_id("ses", rng, id_pool),
                "user_id": user_id,
                "session_ts": ts,
                "completed": rng.random() < completion_p,
                "phase": "in_window",
            })

    sessions_df = pd.DataFrame(session_rows)
    assignments_df = pd.DataFrame(assignment_rows)

    return assignments_df, sessions_df


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------

def generate_orders(sessions_df, assignments_df, seed):
    rng = np.random.default_rng(seed)
    id_pool = set()

    variant_lookup = assignments_df.set_index("user_id")["variant"].to_dict()

    completed = sessions_df[sessions_df["completed"]].copy()

    order_rows = []
    for _, row in completed.iterrows():
        variant = variant_lookup.get(row["user_id"])
        is_in_window = row["phase"] == "in_window"

        # baseline lognormal params (applies to all pre-window orders and control in-window orders)
        # mixture model: most orders are modest, a small fraction are large "big basket" orders,
        # so the overall range realistically spans single digits up to a few thousand dollars.
        mu, sigma = 3.2, 0.6
        big_basket_p = 0.03       # ~3% of orders are large baskets
        big_basket_mult_low, big_basket_mult_high = 8, 35

        unit_lambda = 2.3

        if is_in_window and variant == "treatment":
            mu += 0.09           # AOV lift for treatment, baked in
            unit_lambda += 0.35  # UPT lift for treatment, baked in

        amount = float(rng.lognormal(mean=mu, sigma=sigma))
        is_big_basket = rng.random() < big_basket_p
        if is_big_basket:
            amount *= rng.uniform(big_basket_mult_low, big_basket_mult_high)
        amount = round(min(max(amount, 3.0), 4000.0), 2)  # keep within a realistic floor/ceiling

        if is_big_basket:
            units = int(rng.uniform(15, 80))  # bulk trip: same event that drove the amount up
        else:
            units = max(1, int(rng.poisson(unit_lambda)))

        order_rows.append({
            "order_id": make_id("ord", rng, id_pool),
            "session_id": row["session_id"],
            "order_amount": amount,
            "unit_count": units,
            "order_date": row["session_ts"].date().isoformat(),
        })

    orders_df = pd.DataFrame(order_rows)
    return orders_df


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    users_df = generate_users(N_USERS, seed=MASTER_SEED + 1)
    assignments_df, sessions_df = generate_assignments_and_sessions(
        users_df, seed=MASTER_SEED + 2
    )
    orders_df = generate_orders(sessions_df, assignments_df, seed=MASTER_SEED + 3)

    # drop the internal "phase" helper column before writing -- not part of the schema
    sessions_out = sessions_df.drop(columns=["phase"]).copy()
    sessions_out["session_ts"] = sessions_out["session_ts"].astype(str)

    assignments_out = assignments_df.copy()
    assignments_out["first_assigned_ts"] = assignments_out["first_assigned_ts"].astype(str)

    # Relative to wherever this script is run from -- run it from the project
    # root (upsell-ab-test/) so this resolves to upsell-ab-test/data/raw
    out_dir = "../data/raw"
    users_df.to_csv(f"{out_dir}/users.csv", index=False)
    assignments_out.to_csv(f"{out_dir}/experiment_assignments.csv", index=False)
    sessions_out.to_csv(f"{out_dir}/checkout_sessions.csv", index=False)
    orders_df.to_csv(f"{out_dir}/orders.csv", index=False)

    print("Generation complete.")
    print(f"  users:                  {len(users_df):,} rows")
    print(f"  experiment_assignments: {len(assignments_df):,} rows")
    print(f"  checkout_sessions:      {len(sessions_df):,} rows")
    print(f"  orders:                 {len(orders_df):,} rows")


if __name__ == "__main__":
    main()