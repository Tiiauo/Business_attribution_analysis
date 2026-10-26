-- Run market-quality.sql first: conflicting duplicate facts must fail validation.
WITH periods(period, lo, hi) AS (
 VALUES ('base','2026-09-01T00:00:00+08:00'::timestamptz,'2026-09-08T00:00:00+08:00'::timestamptz),
        ('current','2026-09-08T00:00:00+08:00'::timestamptz,'2026-09-15T00:00:00+08:00'::timestamptz)
), facts AS (SELECT DISTINCT order_id,user_id,created_at,paid_at,status,channel_id,paid_amount,currency FROM market.orders),
revenue AS (
 SELECT p.period,coalesce(o.channel_id,'未归因') AS channel_id,count(*) AS orders,sum(o.paid_amount) AS revenue
 FROM periods p JOIN facts o ON o.paid_at>=p.lo AND o.paid_at<p.hi
 WHERE o.status IN ('paid','completed') AND o.currency='CNY' GROUP BY p.period,coalesce(o.channel_id,'未归因')
), spend AS (
 SELECT p.period,coalesce(s.channel_id,'未归因') AS channel_id,sum(s.amount) AS spend
 FROM periods p JOIN market.ad_spend s ON s.attributed_date >= (p.lo AT TIME ZONE 'Asia/Shanghai')::date
 AND s.attributed_date < (p.hi AT TIME ZONE 'Asia/Shanghai')::date GROUP BY p.period,coalesce(s.channel_id,'未归因')
)
SELECT coalesce(r.period,s.period) AS period,coalesce(r.channel_id,s.channel_id) AS channel_id,
coalesce(r.orders,0) AS orders,coalesce(r.revenue,0) AS revenue,coalesce(s.spend,0) AS spend,
coalesce(r.revenue,0)/nullif(s.spend,0) AS roas,s.spend/nullif(r.orders,0) AS acquisition_cost
FROM revenue r FULL JOIN spend s USING(period,channel_id) ORDER BY period,channel_id;
