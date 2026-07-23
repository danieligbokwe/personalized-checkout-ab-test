-- Classify each user as frequent or occasional using orders from 90 days prior to experiment
WITH segmented_users AS (
    SELECT
        u.user_id,
        -- count(o.order_id), not count(*) -- LEFT JOIN produces one NULL order row per
        -- unmatched session; count(*) would count those rows and misclassify browsers
        -- with zero purchases as "frequent"
        CASE WHEN count(o.order_id) >= 3 THEN 'frequent' ELSE 'occasional' END AS segment
    FROM users u
    INNER JOIN experiment_assignments a
             ON u.user_id = a.user_id
    INNER JOIN checkout_sessions s
             ON u.user_id = s.user_id
    -- LEFT JOIN (not INNER) so users with zero prior orders are kept and correctly
    -- counted as 0, rather than dropped from the result entirely
    LEFT JOIN orders o
             ON s.session_id = o.session_id AND
                o.order_date >= a.first_assigned_ts::date - INTERVAL '90 days' AND
                o.order_date < a.first_assigned_ts
    GROUP BY u.user_id
),
-- analyze user checkout sessions during the experiment
experiment_sessions AS (
        SELECT
            u.user_id,
            count(s.session_id) total_sessions,
            SUM(CASE WHEN s.completed THEN 1 ELSE 0 END) AS total_completed_sessions,
            SUM(CASE WHEN NOT s.completed THEN 1 ELSE 0 END) AS total_abandoned_sessions
        FROM users u
        INNER JOIN experiment_assignments a
            ON u.user_id = a.user_id
        -- INNER JOIN is safe here: every assigned user has >= 1 in-window session by
        -- definition, since first_assigned_ts IS the timestamp of their first one
        INNER JOIN checkout_sessions s
            ON u.user_id = s.user_id AND
               s.session_ts >= a.first_assigned_ts
        GROUP BY u.user_id
),
-- analyze user orders during the experiment
user_orders AS (
    SELECT
        u.user_id,
        count(o.order_id) total_orders,
        sum(coalesce(o.order_amount, 0)) total_spend,
        sum(coalesce(o.unit_count, 0)) total_units
    FROM users u
    INNER JOIN experiment_assignments a
        ON u.user_id = a.user_id
    INNER JOIN checkout_sessions s
        ON u.user_id = s.user_id AND
           s.session_ts >= a.first_assigned_ts
    -- LEFT JOIN so users who checked out but abandoned every session (never ordered)
    -- are kept with total_orders = 0 rather than dropped
    LEFT JOIN orders o
        ON o.session_id = s.session_id AND
           -- cast to ::date: order_date is a DATE (implicitly midnight) while
           -- first_assigned_ts is a TIMESTAMP -- without the cast, a same-day order
           -- placed after the exact assignment time gets wrongly excluded, since
           -- midnight < assignment time even though the order came after assignment
           o.order_date >= a.first_assigned_ts::date
    GROUP BY u.user_id
)
--Join CTES to users to generate user-level aggregated table
SELECT
    u.user_id,
    a.variant,
    su.segment,
    es.total_sessions,
    es.total_completed_sessions,
    es.total_abandoned_sessions,
    uo.total_orders,
    uo.total_spend,
    uo.total_units
FROM users u
INNER JOIN experiment_assignments a
    ON u.user_id = a.user_id
-- INNER JOIN is safe across all three CTEs: none of them filter out any assigned
-- user (their LEFT JOINs on orders keep everyone, just with 0s where there's no match)
INNER JOIN segmented_users su
    ON u.user_id = su.user_id
INNER JOIN experiment_sessions es
    ON u.user_id = es.user_id
INNER JOIN user_orders uo
    ON u.user_id = uo.user_id;

-- Create order-level table for metrics like AOV (Average Order Value) and UPT (Units Per Transaction)
-- Kept separate from the user-level table above: AOV/UPT are transaction-level metrics,
-- and averaging per-user averages would distort them if users place different numbers
-- of orders -- every order needs to count once, equally, not be pre-averaged per user.
SELECT
    a.variant,
    o.order_id,
    o.order_amount,
    o.unit_count
FROM orders o
INNER JOIN checkout_sessions s
    ON o.session_id = s.session_id
INNER JOIN users u
    ON s.user_id = u.user_id
INNER JOIN experiment_assignments a
    ON s.user_id = a.user_id
WHERE o.order_date >= a.first_assigned_ts::date;