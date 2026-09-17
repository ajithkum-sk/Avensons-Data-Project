-- =====================================================================
-- PLACEHOLDER mapping, only so the category-coverage rule (5.6) can be
-- demonstrated before Avensons answers Q5/Q6. Replace the mapping below
-- with the real salesman -> product group list, then re-run.
--
-- RUN THIS AFTER THE FIRST FILE IS LOADED. dim_salesman is populated during
-- ingest, so on an empty database this script maps nothing and rule R06
-- returns zero rows.
-- =====================================================================
SET search_path = sg, public;

INSERT INTO salesman_category (salesman_key, category)
SELECT s.salesman_key,
       CASE
         WHEN s.salesman_type ILIKE '%Grocery%'     THEN 'ATTA'
         WHEN s.salesman_type ILIKE '%FMCG Van%'    THEN 'AGARBATHI'
         WHEN s.salesman_type ILIKE '%Convenience%' THEN 'FOODS'
         ELSE 'PCP'
       END
FROM dim_salesman s
ON CONFLICT (salesman_key) DO UPDATE SET category = EXCLUDED.category;
