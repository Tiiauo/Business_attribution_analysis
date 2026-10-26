-- Each query stands alone and can be run with that schema's read-only account.
SELECT date_trunc('week',i.occurred_at AT TIME ZONE 'Asia/Shanghai') AS week,p.name,count(DISTINCT i.event_id) AS impressions,
 count(DISTINCT c.event_id) AS clicks,count(DISTINCT v.conversion_id) AS conversions,
 count(DISTINCT c.event_id)::numeric/nullif(count(DISTINCT i.event_id),0) AS ctr
FROM catalog.products p JOIN catalog.categories k USING(category_id) JOIN catalog.search_impressions i USING(product_id)
LEFT JOIN catalog.clicks c ON c.impression_id=i.event_id LEFT JOIN catalog.conversions v ON v.click_id=c.event_id
GROUP BY week,p.name ORDER BY week;

SELECT s.product_id,s.warehouse_id,s.quantity AS opening,n.quantity AS closing,i.quantity AS inbound,o.quantity AS outbound,
 n.quantity-(s.quantity+i.quantity-o.quantity) AS discrepancy,v.quantity AS sales
FROM inventory.stocks s JOIN inventory.stocks n USING(product_id,warehouse_id)
JOIN inventory.inbound i ON i.product_id=s.product_id AND i.warehouse_id=s.warehouse_id AND i.occurred_at>s.snapshot_at AND i.occurred_at<n.snapshot_at
JOIN inventory.outbound o ON o.product_id=s.product_id AND o.warehouse_id=s.warehouse_id AND o.occurred_at>s.snapshot_at AND o.occurred_at<n.snapshot_at
JOIN inventory.sales v ON v.outbound_id=o.document_id WHERE s.snapshot_at<n.snapshot_at;

SELECT p.name,r.review_id,r.content,a.score,coalesce(f.amount,0) AS refunded_amount
FROM feedback.products p JOIN feedback.reviews r USING(product_id) JOIN feedback.ratings a USING(review_id)
LEFT JOIN (SELECT review_id,sum(amount) AS amount FROM feedback.refunds GROUP BY review_id) f USING(review_id);

SELECT u.segment,r.description,count(*) AS partial_refunds,count(DISTINCT o.order_id) AS orders,sum(q.amount) AS refund_amount
FROM refund.customers u JOIN refund.orders o USING(user_id) JOIN refund.requests q USING(order_id)
JOIN refund.reasons r USING(reason_code) WHERE q.status='completed' GROUP BY u.segment,r.description;
