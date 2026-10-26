SELECT
 (SELECT count(*) FROM (SELECT order_id FROM market.orders GROUP BY order_id HAVING count(DISTINCT ROW(user_id,created_at,paid_at,status,channel_id,paid_amount,currency))>1) conflicts) AS conflicting_orders,
 count(*) FILTER(WHERE status IN ('paid','completed') AND paid_at IS NULL) AS missing_paid_at
FROM market.orders;
