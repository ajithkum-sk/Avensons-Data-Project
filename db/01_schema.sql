-- =====================================================================
-- Avensons SalesGuard : schema
-- Postgres 14+  (tested on 16.15)
-- Run as:  psql -U salesguard -d salesguard -f db/01_schema.sql
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS sg;
SET search_path = sg, public;

-- ---------------------------------------------------------------- masters

CREATE TABLE IF NOT EXISTS dim_salesman (
    salesman_key   serial PRIMARY KEY,
    salesman_name  text NOT NULL UNIQUE,
    salesman_type  text,
    product_group  text,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dim_beat (
    beat_key       serial PRIMARY KEY,
    raw_beat       text NOT NULL UNIQUE,   -- '164 - 10FPP'
    beat_code      text,                   -- '164'
    beat_name      text,                   -- '10FPP'
    location       text,                   -- CBE / TIRUPPUR / ISS ... (derived, editable)
    -- beats whose name ends in 'PP' tolerate more due days (client rule)
    due_tolerance_days integer NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_code   text PRIMARY KEY,
    customer_name   text,
    channel_type    text,
    outlet_type     text,
    loyalty_program text,
    credit_term     integer NOT NULL DEFAULT 0,
    first_seen_on   date,
    last_seen_on    date
);

CREATE TABLE IF NOT EXISTS dim_category (
    category   text PRIMARY KEY,           -- ATTA, AGARBATHI, ...
    label      text
);

-- which categories are expected to co-exist in one outlet (rule 5.6)
CREATE TABLE IF NOT EXISTS category_affinity (
    category          text NOT NULL REFERENCES dim_category(category) ON DELETE CASCADE,
    expects_category  text NOT NULL REFERENCES dim_category(category) ON DELETE CASCADE,
    PRIMARY KEY (category, expects_category),
    CHECK (category <> expects_category)
);

CREATE TABLE IF NOT EXISTS salesman_category (
    salesman_key integer PRIMARY KEY REFERENCES dim_salesman ON DELETE CASCADE,
    category     text NOT NULL REFERENCES dim_category(category)
);

-- ---------------------------------------------------------------- facts

CREATE TABLE IF NOT EXISTS snapshot (
    snapshot_id   serial PRIMARY KEY,
    snapshot_date date NOT NULL,
    snapshot_type text NOT NULL CHECK (snapshot_type IN ('daily','monthly')),
    source_file   text,
    printed_at    timestamptz,
    row_count     integer NOT NULL DEFAULT 0,
    loaded_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (snapshot_date, snapshot_type)
);

CREATE TABLE IF NOT EXISTS outstanding_document (
    doc_key        bigserial PRIMARY KEY,
    snapshot_id    integer NOT NULL REFERENCES snapshot ON DELETE CASCADE,
    customer_code  text    NOT NULL REFERENCES dim_customer,
    salesman_key   integer          REFERENCES dim_salesman,
    beat_key       integer          REFERENCES dim_beat,
    document_type  text    NOT NULL,          -- INV / CRN / DBN / OPN
    document_no    text    NOT NULL,
    document_date  date,
    amount         numeric(14,2) NOT NULL DEFAULT 0,
    balance        numeric(14,2) NOT NULL DEFAULT 0,
    due_days       integer,                   -- = as_on_date - document_date
    overdue_days   integer,                   -- = due_days - credit grace (NULL on DBN/OPN)
    invoice_status text,
    source_ignored boolean NOT NULL DEFAULT false,  -- 'RULE BOOK' = IGNORE in the source file
    UNIQUE (snapshot_id, customer_code, document_type, document_no)
);

CREATE INDEX IF NOT EXISTS ix_doc_snap_cust ON outstanding_document (snapshot_id, customer_code);
CREATE INDEX IF NOT EXISTS ix_doc_snap_sman ON outstanding_document (snapshot_id, salesman_key);
CREATE INDEX IF NOT EXISTS ix_doc_type      ON outstanding_document (snapshot_id, document_type);

-- monthly sales, shop-wise + salesman-wise (uploaded once a month)
CREATE TABLE IF NOT EXISTS monthly_sales (
    month_start    date    NOT NULL,
    customer_code  text    NOT NULL REFERENCES dim_customer,
    salesman_key   integer NOT NULL REFERENCES dim_salesman,
    category       text,
    invoice_count  integer NOT NULL DEFAULT 0,
    total_amount   numeric(14,2) NOT NULL DEFAULT 0,
    source         text NOT NULL DEFAULT 'upload',
    PRIMARY KEY (month_start, customer_code, salesman_key)
);

CREATE INDEX IF NOT EXISTS ix_ms_cust ON monthly_sales (customer_code, month_start);

-- ---------------------------------------------------------------- rule config

CREATE TABLE IF NOT EXISTS rule (
    rule_code    text PRIMARY KEY,
    title        text NOT NULL,
    purpose      text,
    audience     text NOT NULL DEFAULT 'manager',   -- manager | salesman | both
    rule_group   text NOT NULL DEFAULT 'credit',    -- credit | discipline | opportunity | global
    severity     text NOT NULL DEFAULT 'medium',    -- low | medium | high
    is_enabled   boolean NOT NULL DEFAULT true,
    needs_sales_data boolean NOT NULL DEFAULT false,
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    sort_order   integer NOT NULL DEFAULT 100,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- global filters live in one row so every rule reads the same switches
CREATE TABLE IF NOT EXISTS global_config (
    id     boolean PRIMARY KEY DEFAULT true CHECK (id),
    params jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- 5.10 Multiple-Billing Exception: suppress a rule for a customer/salesman pair
CREATE TABLE IF NOT EXISTS rule_exception (
    exception_id  serial PRIMARY KEY,
    rule_code     text    NOT NULL REFERENCES rule ON DELETE CASCADE,
    customer_code text             REFERENCES dim_customer,
    salesman_key  integer          REFERENCES dim_salesman,
    note          text,
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (customer_code IS NOT NULL OR salesman_key IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_exc_lookup ON rule_exception (rule_code, is_active);

-- ---------------------------------------------------------------- results

CREATE TABLE IF NOT EXISTS rule_run (
    run_id       serial PRIMARY KEY,
    snapshot_id  integer NOT NULL REFERENCES snapshot ON DELETE CASCADE,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL DEFAULT 'running',  -- running | success | failed
    scope_rows   integer,
    global_params jsonb,
    error        text
);

CREATE TABLE IF NOT EXISTS rule_run_detail (
    run_id     integer NOT NULL REFERENCES rule_run ON DELETE CASCADE,
    rule_code  text    NOT NULL REFERENCES rule,
    status     text    NOT NULL,          -- success | skipped | failed
    hits       integer NOT NULL DEFAULT 0,
    ms         integer,
    params     jsonb,
    message    text,
    PRIMARY KEY (run_id, rule_code)
);

CREATE TABLE IF NOT EXISTS rule_violation (
    violation_id  bigserial PRIMARY KEY,
    run_id        integer NOT NULL REFERENCES rule_run ON DELETE CASCADE,
    rule_code     text    NOT NULL REFERENCES rule,
    customer_code text    NOT NULL REFERENCES dim_customer,
    salesman_key  integer          REFERENCES dim_salesman,
    doc_key       bigint,
    document_no   text,
    document_date date,
    amount        numeric(14,2),
    balance       numeric(14,2),
    overdue_days  integer,
    severity      text,
    reason        text NOT NULL,
    details       jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_viol_run_rule ON rule_violation (run_id, rule_code);
CREATE INDEX IF NOT EXISTS ix_viol_cust     ON rule_violation (run_id, customer_code);
CREATE INDEX IF NOT EXISTS ix_viol_sman     ON rule_violation (run_id, salesman_key);

-- salesperson explanation against a flagged document (role: Salesperson)
CREATE TABLE IF NOT EXISTS violation_note (
    note_id      serial PRIMARY KEY,
    violation_id bigint NOT NULL REFERENCES rule_violation ON DELETE CASCADE,
    author       text   NOT NULL,
    note         text   NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_user (
    user_id    serial PRIMARY KEY,
    username   text NOT NULL UNIQUE,
    full_name  text,
    role       text NOT NULL CHECK (role IN ('super_admin','manager','salesman')),
    salesman_key integer REFERENCES dim_salesman,
    is_active  boolean NOT NULL DEFAULT true
);

-- ---------------------------------------------------------------- scope function
-- One place that applies the GLOBAL filters (5.11 small bill, 5.12 cigarette
-- exclusion) and computes effective overdue days. Every rule selects from here,
-- so a change to a global filter changes all rules at once.

CREATE OR REPLACE FUNCTION sg.f_scope(
    p_snapshot_id   integer,
    p_min_balance   numeric DEFAULT 10,
    p_exclude_cig   boolean DEFAULT true,
    p_cig_prefixes  text[]  DEFAULT ARRAY['TH'],
    p_cig_salesmen  text[]  DEFAULT ARRAY['MANIKANDAN VAN','NANDAKUMAR VAN'],
    p_cig_locations text[]  DEFAULT ARRAY['CBE'],
    p_use_source_ignore boolean DEFAULT false,
    p_pp_tolerance_days integer DEFAULT 0,
    p_pp_salesman_types text[] DEFAULT ARRAY['Grocery 2 Ds']
) RETURNS TABLE (
    doc_key        bigint,
    snapshot_id    integer,
    customer_code  text,
    customer_name  text,
    channel_type   text,
    outlet_type    text,
    credit_term    integer,
    salesman_key   integer,
    salesman_name  text,
    salesman_type  text,
    beat_key       integer,
    raw_beat       text,
    location       text,
    document_type  text,
    document_no    text,
    document_date  date,
    amount         numeric,
    balance        numeric,
    due_days       integer,
    overdue_days   integer,
    eff_overdue    integer,   -- overdue_days, falling back to due_days, minus PP tolerance
    tolerance_days integer,
    invoice_status text
) LANGUAGE sql STABLE AS $$
    WITH tol AS (
        SELECT d.doc_key,
               CASE WHEN p_pp_tolerance_days > 0
                     AND upper(coalesce(b.beat_name,'')) LIKE '%PP'
                     AND (p_pp_salesman_types IS NULL
                          OR s.salesman_type = ANY (p_pp_salesman_types))
                    THEN p_pp_tolerance_days ELSE 0 END AS tolerance_days
        FROM outstanding_document d
        LEFT JOIN dim_beat     b ON b.beat_key = d.beat_key
        LEFT JOIN dim_salesman s ON s.salesman_key = d.salesman_key
        WHERE d.snapshot_id = p_snapshot_id
    )
    SELECT d.doc_key, d.snapshot_id, d.customer_code, c.customer_name,
           c.channel_type, c.outlet_type, c.credit_term,
           d.salesman_key, s.salesman_name, s.salesman_type,
           d.beat_key, b.raw_beat, b.location,
           d.document_type, d.document_no, d.document_date,
           d.amount, d.balance, d.due_days, d.overdue_days,
           GREATEST(coalesce(d.overdue_days, d.due_days, 0) - t.tolerance_days, 0) AS eff_overdue,
           t.tolerance_days,
           d.invoice_status
    FROM outstanding_document d
    JOIN tol t                ON t.doc_key = d.doc_key
    JOIN dim_customer c       ON c.customer_code = d.customer_code
    LEFT JOIN dim_salesman s  ON s.salesman_key = d.salesman_key
    LEFT JOIN dim_beat b      ON b.beat_key = d.beat_key
    WHERE d.snapshot_id = p_snapshot_id
      -- 5.11 Small-Bill Ignore (balance header column)
      AND abs(d.balance) > p_min_balance
      -- source file already carries an IGNORE marker; optional second opinion
      AND (NOT p_use_source_ignore OR NOT d.source_ignored)
      -- 5.12 Cigarette Exclusion, only for the configured locations
      AND NOT (
            p_exclude_cig
        AND (p_cig_locations IS NULL OR coalesce(b.location,'CBE') = ANY (p_cig_locations))
        AND (
              upper(coalesce(s.salesman_name,'')) = ANY (SELECT upper(x) FROM unnest(p_cig_salesmen) x)
           OR EXISTS (SELECT 1 FROM unnest(p_cig_prefixes) px
                       WHERE upper(coalesce(s.salesman_name,'')) LIKE upper(px) || '%')
        )
      );
$$;

-- ---------------------------------------------------------------- read views

CREATE OR REPLACE VIEW sg.v_violation AS
SELECT v.violation_id, v.run_id, v.rule_code, r.title AS rule_title, r.audience,
       r.rule_group, v.severity, v.customer_code, c.customer_name, c.channel_type,
       c.outlet_type, v.salesman_key, s.salesman_name, s.salesman_type,
       b.raw_beat, b.location, v.document_no, v.document_date, v.amount, v.balance,
       v.overdue_days, v.reason, v.details,
       (SELECT count(*) FROM violation_note n WHERE n.violation_id = v.violation_id) AS note_count
FROM rule_violation v
JOIN rule r            ON r.rule_code = v.rule_code
JOIN dim_customer c    ON c.customer_code = v.customer_code
LEFT JOIN dim_salesman s ON s.salesman_key = v.salesman_key
LEFT JOIN outstanding_document d ON d.doc_key = v.doc_key
LEFT JOIN dim_beat b   ON b.beat_key = d.beat_key;

CREATE OR REPLACE VIEW sg.v_latest_run AS
SELECT * FROM rule_run WHERE status = 'success'
ORDER BY snapshot_id DESC, run_id DESC LIMIT 1;
