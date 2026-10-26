-- Strict within-period chains; event IDs deduplicated before counting users.
WITH periods(period, lo, hi) AS (
 VALUES ('base','2026-09-01T00:00:00+08:00'::timestamptz,'2026-09-08T00:00:00+08:00'::timestamptz),
        ('current','2026-09-08T00:00:00+08:00'::timestamptz,'2026-09-15T00:00:00+08:00'::timestamptz)
), visits AS (
 SELECT DISTINCT ON (event_id) * FROM behavior.visit_events ORDER BY event_id
), carts AS (
 SELECT DISTINCT ON (event_id) * FROM behavior.cart_events ORDER BY event_id
), orders AS (
 SELECT DISTINCT ON (event_id) * FROM behavior.order_events ORDER BY event_id
), first_visit AS (
 SELECT DISTINCT ON (p.period,v.user_id) p.period,p.lo,p.hi,v.user_id,v.device,v.occurred_at
 FROM periods p JOIN visits v ON v.occurred_at>=p.lo AND v.occurred_at<p.hi
 ORDER BY p.period,v.user_id,v.occurred_at,v.event_id
), flags AS (
 SELECT v.*, EXISTS(SELECT 1 FROM carts c WHERE c.user_id=v.user_id AND c.occurred_at>v.occurred_at AND c.occurred_at<v.hi) AS cart,
 EXISTS(SELECT 1 FROM carts c JOIN orders o ON o.user_id=c.user_id AND o.occurred_at>c.occurred_at
 WHERE c.user_id=v.user_id AND c.occurred_at>v.occurred_at AND c.occurred_at<v.hi
 AND o.occurred_at<v.hi AND o.status IN ('paid','completed')) AS ordered
 FROM first_visit v
)
SELECT period,device,count(*) AS visits,count(*) FILTER(WHERE cart) AS carts,count(*) FILTER(WHERE ordered) AS orders
FROM flags GROUP BY period,device ORDER BY period,device;
