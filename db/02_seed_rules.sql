-- =====================================================================
-- Avensons SalesGuard : rule catalogue + default parameters
-- Every threshold here is editable from the UI (Settings screen) - no code change.
-- =====================================================================
SET search_path = sg, public;

INSERT INTO global_config (id, params) VALUES (true, '{
  "min_balance": 10,
  "exclude_cigarette": true,
  "cig_salesman_prefixes": ["TH"],
  "cig_salesman_names": ["MANIKANDAN VAN", "NANDAKUMAR VAN"],
  "cig_locations": ["CBE"],
  "use_source_ignore_flag": false,
  "pp_tolerance_days": 0,
  "pp_salesman_types": ["Grocery 2 Ds"],
  "overdue_basis": "overdue_days"
}'::jsonb)
ON CONFLICT (id) DO NOTHING;

INSERT INTO rule (rule_code, title, purpose, audience, rule_group, severity,
                  needs_sales_data, params, sort_order) VALUES

('R01_DBN_OVERDUE', 'DBN overdue alert',
 'Bounced cheque / debit note pending beyond the tolerated age.',
 'manager', 'credit', 'high', false,
 '{"days": 14, "min_amount": 10, "document_types": ["DBN"], "document_no_prefixes": ["DR"]}', 10),

('R02_SAME_SALESMAN_DOUBLE_BILL', 'Same-salesman double bill',
 'A salesman billed the same shop again while his earlier bill is still pending.',
 'both', 'discipline', 'high', false,
 '{"max_overdue_days": 30, "min_balance": 10, "excluded_document_types": ["CRN"], "min_documents": 2}', 20),

('R03_CROSS_SALESMAN_CREDIT_BLOCK', 'Cross-salesman credit flag',
 'Another salesman''s bill for the same shop is severely overdue - flag, do not block.',
 'both', 'credit', 'high', false,
 '{"overdue_days": 30, "min_balance": 10}', 30),

('R04_STOCK_DUMPING', 'Stock-dumping alert',
 'New billing is far above what the outlet normally buys.',
 'manager', 'discipline', 'high', true,
 '{"threshold_pct": 10, "months": 3, "average_basis": "monthly", "min_invoice_amount": 1000, "recent_days": 30}', 40),

('R05_UNDERPERFORMING_SALESMAN', 'Underperforming salesman (collection)',
 'One salesman collected and re-billed the shop; his colleague has not collected.',
 'both', 'discipline', 'high', false,
 '{"window_days": 10, "max_overdue_days": 30, "min_balance": 10}', 50),

('R06_LOST_POTENTIAL_SALE', 'Lost potential sale (category coverage)',
 'Shop bought category A recently but the partner category was never billed.',
 'both', 'opportunity', 'medium', false,
 '{"window_days": 10}', 60),

('R07_LOW_OUTSTANDING_RATIO', 'Low outstanding ratio',
 'Shop paid almost everything and left a small tail - check why it is not closed.',
 'manager', 'discipline', 'low', false,
 '{"ratio_pct": 10, "min_invoice_amount": 500}', 70),

('R08_CHRONIC_DEFAULT_CUSTOMER', 'Chronic default customer',
 'Every salesman has exactly one bill on this shop and all of them are stuck.',
 'manager', 'credit', 'high', false,
 '{"overdue_days": 30, "min_salesmen": 2, "min_balance": 10}', 80),

('R09_OVER_30_DAY_INVOICE', 'Over-30-day invoice list',
 'Standard credit-control list.',
 'both', 'credit', 'medium', false,
 '{"overdue_days": 30, "min_balance": 10, "document_types": ["INV","OPN","DBN"]}', 90),

('R14_HIGH_UNCOLLECTED_RATIO', 'High uncollected ratio (80-100%)',
 'Shop-salesman pair older than 30 days with almost nothing collected.',
 'manager', 'credit', 'high', false,
 '{"overdue_days": 30, "min_ratio_pct": 80, "max_ratio_pct": 100, "min_invoice_amount": 500}', 100),

('R15_DORMANT_CUSTOMER', 'Dormant customer',
 'Previously active shop with no fresh billing from anyone.',
 'manager', 'opportunity', 'medium', false,
 '{"dormant_days": 45, "was_active_within_days": 180}', 110),

('R16_SUDDEN_DROP', 'Sudden drop in sales',
 'This month is far below the shop''s own 3-month average.',
 'manager', 'opportunity', 'medium', true,
 '{"drop_below_pct": 50, "months": 3}', 120),

('R17_BEAT_COVERAGE_GAP', 'Beat coverage gap',
 'Shops in a beat with no billing while the rest of the beat was served.',
 'manager', 'opportunity', 'low', false,
 '{"gap_days": 21}', 130),

('R18_NEW_CUSTOMER_RISK', 'New customer risk',
 'First-time shop already overdue.',
 'manager', 'credit', 'high', false,
 '{"new_within_days": 60, "overdue_days": 15, "min_balance": 10}', 140)

ON CONFLICT (rule_code) DO UPDATE
  SET title = EXCLUDED.title,
      purpose = EXCLUDED.purpose,
      audience = EXCLUDED.audience,
      rule_group = EXCLUDED.rule_group,
      severity = EXCLUDED.severity,
      needs_sales_data = EXCLUDED.needs_sales_data,
      sort_order = EXCLUDED.sort_order;
-- note: params are deliberately NOT overwritten on re-run, so Avensons' own
-- tuning survives a redeploy.

-- seed categories (Q5/Q6 - replace with the real 4 product groups)
INSERT INTO dim_category (category, label) VALUES
  ('ATTA','Atta'), ('AGARBATHI','Agarbathi'), ('FOODS','Foods'), ('PCP','Personal care')
ON CONFLICT DO NOTHING;

INSERT INTO category_affinity (category, expects_category) VALUES
  ('ATTA','AGARBATHI'), ('AGARBATHI','ATTA'), ('ATTA','FOODS'), ('FOODS','PCP')
ON CONFLICT DO NOTHING;

INSERT INTO app_user (username, full_name, role) VALUES
  ('admin','Super Admin','super_admin'),
  ('manager','Avensons Manager','manager')
ON CONFLICT DO NOTHING;

-- Rules whose accuracy depends on history we do not have on day 1. The UI
-- shows this note next to the count so nobody trusts a soft number.
ALTER TABLE rule ADD COLUMN IF NOT EXISTS data_note text;

UPDATE rule SET data_note =
  'Sales history is currently derived from unpaid invoices in the outstanding file. '
  'Counts firm up once real monthly sales (shop-wise, salesman-wise) are uploaded.'
WHERE rule_code IN ('R04_STOCK_DUMPING','R16_SUDDEN_DROP');

UPDATE rule SET data_note =
  'Uses the earliest date we have ever seen for the outlet, so it sharpens as daily '
  'snapshots accumulate.'
WHERE rule_code IN ('R15_DORMANT_CUSTOMER','R17_BEAT_COVERAGE_GAP','R18_NEW_CUSTOMER_RISK');

ALTER TABLE rule ADD COLUMN IF NOT EXISTS needs_history boolean NOT NULL DEFAULT false;
UPDATE rule SET needs_history = true WHERE rule_code = 'R18_NEW_CUSTOMER_RISK';
