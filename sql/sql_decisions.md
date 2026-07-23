
# SQL Aggregation Decisions

Brief notes on the non-obvious decisions in the aggregation query.

## Two tables, not one

User-level table (RPRU, abandonment, segment) and order-level table (AOV,
UPT) are kept separate. AOV/UPT are transaction-level metrics — averaging
per-user averages would distort them if users place different numbers of
orders, so every order needs to count once, individually.

## LEFT JOIN on `orders`, not INNER

Users with zero matching orders (no purchases in the segmentation window,
or no purchases at all) need to be kept and counted as zero — not dropped.
An early inner-join-equivalent version silently dropped ~2,000 legitimate
zero-order users.

## Filter conditions go in `ON`, not `WHERE`

A `WHERE` clause filtering on the LEFT-joined table's columns silently turns
it back into an INNER JOIN, since `NULL >= x` is never `true`. Date filters
on `orders` were moved into the `ON` clause to avoid this.

## `COUNT(column)`, not `COUNT(*)`

`COUNT(*)` counts rows regardless of NULLs, so it would count unmatched
(NULL) order rows from a LEFT JOIN as if they were real orders.
`COUNT(o.order_id)` ignores those NULLs, counting only real matches.

## Cast `DATE` to match `TIMESTAMP` granularity

`order_date` is a `DATE`; `first_assigned_ts` is a `TIMESTAMP`. Comparing
them directly treats the date as midnight, which wrongly excluded same-day
orders placed after the actual assignment time. Casting
`first_assigned_ts::date` fixed the comparison.
