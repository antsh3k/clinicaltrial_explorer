-- ct-landscape definition-of-record views (spec §4.3). Every landscape metric is defined exactly ONCE here.
-- Scope filters (interventional, industry, drug/bio) live in these views, never in the raw or entity layers.
-- The build fails if any view returns 0 rows (a silently-empty definition is the likeliest real bug).

-- ---------------------------------------------------------------- v_trials: spine + derived flags
CREATE OR REPLACE VIEW v_trials AS
WITH snap AS (SELECT CAST(value AS DATE) AS snapshot_date FROM build_meta WHERE key = 'snapshot_date'),
lead AS (
  SELECT nct_id, company_id AS lead_company_id, agency_class AS lead_agency_class
  FROM trial_sponsors_norm WHERE role = 'lead'
),
drug AS (
  SELECT DISTINCT nct_id FROM interventions WHERE type IN ('DRUG','BIOLOGICAL','COMBINATION_PRODUCT','GENETIC')
)
SELECT s.nct_id, s.brief_title, s.study_type, s.overall_status, s.phase_norm,
       CASE s.phase_norm WHEN 'EARLY_PHASE1' THEN 0.5 WHEN 'PHASE1' THEN 1.0 WHEN 'PHASE2' THEN 2.0
                         WHEN 'PHASE3' THEN 3.0 WHEN 'PHASE4' THEN 4.0 ELSE NULL END AS phase_rank,
       (drug.nct_id IS NOT NULL) AS is_drug_trial,
       (lead.lead_agency_class = 'INDUSTRY') AS is_industry,
       lead.lead_company_id, lead.lead_agency_class,
       s.overall_status IN ('RECRUITING','NOT_YET_RECRUITING','ENROLLING_BY_INVITATION','ACTIVE_NOT_RECRUITING') AS is_active_readout,
       (s.overall_status IN ('RECRUITING','NOT_YET_RECRUITING','ENROLLING_BY_INVITATION','ACTIVE_NOT_RECRUITING')
        OR (s.overall_status = 'COMPLETED' AND s.completion_date_parsed IS NOT NULL
            AND s.completion_date_parsed >= (snap.snapshot_date - INTERVAL 3 YEAR))) AS program_exists,
       s.overall_status IN ('TERMINATED','WITHDRAWN','SUSPENDED') AS is_inactive,
       s.enrollment_count, s.enrollment_type, s.primary_purpose,
       s.start_date_parsed, s.primary_completion_date_parsed, s.completion_date_parsed,
       s.last_update_date_parsed, s.date_precision, s.has_results,
       s.sex, s.minimum_age, s.maximum_age, s.std_ages, s.healthy_volunteers,
       snap.snapshot_date
FROM studies s
CROSS JOIN snap
LEFT JOIN lead USING (nct_id)
LEFT JOIN drug USING (nct_id);

-- ---------------------------------------------------------------- v_trial_conditions_primary: ONE surface per trial
-- mesh_leaf rows when the trial has any, else its listed rows. Every counting view joins THIS.
CREATE OR REPLACE VIEW v_trial_conditions_primary AS
WITH has_mesh AS (SELECT DISTINCT nct_id FROM trial_conditions_norm WHERE source = 'mesh_leaf')
SELECT DISTINCT tc.nct_id, tc.condition_key, tc.display_name, tc.source
FROM trial_conditions_norm tc
LEFT JOIN has_mesh hm USING (nct_id)
WHERE (hm.nct_id IS NOT NULL AND tc.source = 'mesh_leaf')
   OR (hm.nct_id IS NULL AND tc.source = 'listed');

-- ---------------------------------------------------------------- v_conditions: one row per condition key (catalog)
CREATE OR REPLACE VIEW v_conditions AS
SELECT tc.condition_key, any_value(tc.source) AS source,
       arg_max(tc.display_name, 1) AS display_name,
       count(DISTINCT tc.nct_id) AS n_trials,
       count(DISTINCT tc.nct_id) FILTER (WHERE t.study_type = 'INTERVENTIONAL' AND t.is_drug_trial) AS n_drug_trials,
       (SELECT list(area ORDER BY is_primary DESC, area) FROM condition_areas ca WHERE ca.condition_key = tc.condition_key) AS areas
FROM v_trial_conditions_primary tc
JOIN v_trials t USING (nct_id)
GROUP BY 1;

-- ---------------------------------------------------------------- v_programs: THE Q1/Q2 definition of record
CREATE OR REPLACE VIEW v_programs AS
WITH best AS (
  SELECT ta.asset_id, tc.condition_key, t.lead_company_id, t.phase_rank, t.last_update_date_parsed,
         row_number() OVER (PARTITION BY ta.asset_id, tc.condition_key
                            ORDER BY t.phase_rank DESC NULLS LAST, t.last_update_date_parsed DESC NULLS LAST, ta.nct_id) AS rn
  FROM trial_assets ta
  JOIN v_trials t USING (nct_id)
  JOIN v_trial_conditions_primary tc USING (nct_id)
  WHERE ta.role IN ('subject','unknown') AND t.study_type = 'INTERVENTIONAL'
)
SELECT ta.asset_id, tc.condition_key,
       max(t.phase_rank)                                              AS max_phase_ever,
       max(t.phase_rank) FILTER (WHERE t.program_exists)              AS max_phase_active,
       count(DISTINCT ta.nct_id)                                      AS n_trials,
       count(DISTINCT ta.nct_id) FILTER (WHERE t.program_exists)      AS n_active_trials,
       count(DISTINCT ta.nct_id) FILTER (WHERE t.is_active_readout)   AS n_active_readout_trials,
       count(DISTINCT ta.nct_id) FILTER (WHERE ta.role = 'unknown')   AS n_unknown_role_trials,
       count(DISTINCT ta.nct_id) FILTER (WHERE t.is_industry)         AS n_industry_trials,
       max(t.last_update_date_parsed)                                 AS latest_activity,
       any_value(b.lead_company_id)                                   AS lead_company_of_most_advanced,
       list(DISTINCT ta.nct_id ORDER BY ta.nct_id)                    AS nct_ids       -- FULL enumeration, no caps
FROM trial_assets ta
JOIN v_trials t USING (nct_id)
JOIN v_trial_conditions_primary tc USING (nct_id)   -- condition-matched BY CONSTRUCTION; one surface per trial
LEFT JOIN best b ON b.asset_id = ta.asset_id AND b.condition_key = tc.condition_key AND b.rn = 1
WHERE ta.role IN ('subject','unknown')              -- comparators excluded; unknown retained (three-valued)
  AND t.study_type = 'INTERVENTIONAL'               -- observational / EA visible elsewhere, not "development"
GROUP BY 1, 2;

-- ---------------------------------------------------------------- v_asset_max_phase: global max per asset (thin wrapper)
CREATE OR REPLACE VIEW v_asset_max_phase AS
SELECT asset_id, max(max_phase_ever) AS max_phase_ever, max(max_phase_active) AS max_phase_active,
       sum(n_trials) AS n_program_trial_rows, count(*) AS n_conditions
FROM v_programs GROUP BY 1;

-- ---------------------------------------------------------------- v_assets: catalog with counts (resolve_entity ranks on n_trials)
CREATE OR REPLACE VIEW v_assets AS
SELECT a.asset_id, a.canonical_name, a.dedup_key, a.is_combo,
       count(DISTINCT ta.nct_id) AS n_trials,
       count(DISTINCT ta.nct_id) FILTER (WHERE ta.role = 'subject') AS n_subject_trials,
       count(DISTINCT ta.nct_id) FILTER (WHERE ta.role = 'comparator') AS n_comparator_trials,
       count(DISTINCT ta.nct_id) FILTER (WHERE t.is_industry AND t.study_type = 'INTERVENTIONAL') AS n_industry_interventional_trials,
       max(t.phase_rank) AS max_phase_any_role
FROM assets a
LEFT JOIN trial_assets ta USING (asset_id)
LEFT JOIN v_trials t USING (nct_id)
GROUP BY 1, 2, 3, 4;

-- ---------------------------------------------------------------- v_sponsor_activity: Q3 (lead sponsor × area)
CREATE OR REPLACE VIEW v_sponsor_activity AS
WITH trial_area AS (
  SELECT DISTINCT tc.nct_id, coalesce(ca.area, 'Unclassified') AS area, coalesce(ca.is_primary, TRUE) AS is_primary
  FROM v_trial_conditions_primary tc
  LEFT JOIN condition_areas ca USING (condition_key)
)
SELECT t.lead_company_id AS company_id, c.canonical_name AS company_name, t.lead_agency_class AS agency_class, ta.area,
       count(DISTINCT t.nct_id)                                                    AS n_trials,
       count(DISTINCT t.nct_id) FILTER (WHERE t.program_exists)                    AS n_active_trials,
       count(DISTINCT t.nct_id) FILTER (WHERE t.is_active_readout)                 AS n_active_readout_trials,
       count(DISTINCT t.nct_id) FILTER (WHERE ta.is_primary)                       AS n_trials_primary_area,
       count(DISTINCT tas.asset_id) FILTER (WHERE tas.role IN ('subject','unknown')) AS n_assets,
       count(DISTINCT t.nct_id) FILTER (WHERE t.phase_rank >= 3)                   AS n_phase3_plus,
       count(DISTINCT t.nct_id) FILTER (WHERE t.phase_rank = 2)                    AS n_phase2,
       count(DISTINCT t.nct_id) FILTER (WHERE t.phase_rank <= 1)                   AS n_phase1_or_earlier,
       max(t.last_update_date_parsed)                                              AS latest_activity,
       list(DISTINCT t.nct_id ORDER BY t.nct_id)                                   AS nct_ids
FROM v_trials t
JOIN companies c ON c.company_id = t.lead_company_id
JOIN trial_area ta ON ta.nct_id = t.nct_id
LEFT JOIN trial_assets tas ON tas.nct_id = t.nct_id
WHERE t.study_type = 'INTERVENTIONAL' AND t.is_drug_trial
GROUP BY 1, 2, 3, 4;

-- ---------------------------------------------------------------- v_asset_sponsors: per asset, lead sponsors + originator PROXY
CREATE OR REPLACE VIEW v_asset_sponsors AS
WITH per AS (
  SELECT ta.asset_id, t.lead_company_id AS company_id, t.lead_agency_class AS agency_class,
         count(DISTINCT ta.nct_id) AS n_trials, min(t.start_date_parsed) AS first_start, max(t.start_date_parsed) AS last_start
  FROM trial_assets ta JOIN v_trials t USING (nct_id)
  WHERE ta.role IN ('subject','unknown') AND t.lead_company_id IS NOT NULL
  GROUP BY 1, 2, 3
)
SELECT p.asset_id, p.company_id, c.canonical_name AS company_name, p.agency_class, p.n_trials, p.first_start, p.last_start,
       (p.agency_class = 'INDUSTRY' AND p.company_id = first_value(p.company_id) OVER (
          PARTITION BY p.asset_id ORDER BY (p.agency_class = 'INDUSTRY') DESC, p.first_start NULLS LAST, p.n_trials DESC, p.company_id
        )) AS originator_proxy     -- earliest industry lead sponsor: NOT ownership (licensing/M&A invisible to the registry)
FROM per p JOIN companies c USING (company_id);

-- ---------------------------------------------------------------- v_moa: provenance waterfall (chembl > nlm_class > llm)
CREATE OR REPLACE VIEW v_moa AS
SELECT asset_id, 'chembl' AS provenance, 1 AS tier,
       mechanism_of_action AS moa_label, action_type AS action, target_symbols AS targets, NULL AS modality,
       moa_key                                   -- App. B.7 fold, computed in Python at load time
FROM chembl_moa
UNION ALL
SELECT asset_id, 'nlm_class' AS provenance, 2 AS tier,
       class_term AS moa_label, NULL AS action, NULL AS targets, NULL AS modality,
       moa_key
FROM asset_nlm_classes
UNION ALL
SELECT asset_id, 'llm' AS provenance, 3 AS tier,
       coalesce(moa_class, array_to_string(targets_canonical, ', ')) AS moa_label, action,
       CASE WHEN len(targets_canonical) > 0 THEN targets_canonical ELSE targets_raw END AS targets, modality,
       moa_key
FROM asset_enrichment WHERE NOT abstained;

-- best tier per asset (consumers take the highest tier; nothing overwrites anything)
CREATE OR REPLACE VIEW v_moa_best AS
SELECT * FROM v_moa m WHERE tier = (SELECT min(tier) FROM v_moa x WHERE x.asset_id = m.asset_id);

-- ---------------------------------------------------------------- v_moa_trials: Q5 — mechanism → assets → trials (by condition)
CREATE OR REPLACE VIEW v_moa_trials AS
SELECT m.moa_key, m.provenance, m.moa_label, ta.asset_id, tc.condition_key, ta.nct_id, ta.role,
       t.phase_norm, t.phase_rank, t.overall_status, t.program_exists, t.is_industry, t.lead_company_id
FROM v_moa m
JOIN trial_assets ta USING (asset_id)
JOIN v_trials t USING (nct_id)
JOIN v_trial_conditions_primary tc USING (nct_id)
WHERE t.study_type = 'INTERVENTIONAL' AND m.moa_key <> '';

-- ---------------------------------------------------------------- v_combos: arm-level UNION name-level
CREATE OR REPLACE VIEW v_combos AS
WITH arm_level AS (
  SELECT ai.nct_id, ai.arm_no, list(DISTINCT ta.asset_id ORDER BY ta.asset_id) AS asset_ids,
         bool_or(coalesce(ta.in_all_arms, FALSE)) AS has_background
  FROM arm_interventions ai
  JOIN trial_assets ta ON ta.nct_id = ai.nct_id AND ta.intervention_no = ai.intervention_no AND ta.via = 'name'
  JOIN arms a ON a.nct_id = ai.nct_id AND a.arm_no = ai.arm_no
  WHERE a.type = 'EXPERIMENTAL'
  GROUP BY 1, 2
  HAVING count(DISTINCT ta.asset_id) >= 2
),
name_level AS (
  SELECT ta.nct_id, -1 AS arm_no, list(DISTINCT ac.component_asset_id ORDER BY ac.component_asset_id) AS asset_ids,
         bool_or(coalesce(ta.in_all_arms, FALSE)) AS has_background
  FROM trial_assets ta
  JOIN asset_components ac ON ac.combo_asset_id = ta.asset_id
  WHERE ta.via = 'name' AND ta.role IN ('subject','unknown')
  GROUP BY ta.nct_id, ta.asset_id
),
u AS (
  SELECT nct_id, arm_no, asset_ids, 'arm' AS source, has_background FROM arm_level
  UNION ALL
  SELECT nct_id, arm_no, asset_ids, 'name' AS source, has_background FROM name_level
)
SELECT DISTINCT u.nct_id, tc.condition_key, u.asset_ids, u.source, u.has_background, u.arm_no,
       t.phase_norm, t.phase_rank, t.overall_status, t.program_exists, t.is_industry
FROM u
JOIN v_trials t USING (nct_id)
JOIN v_trial_conditions_primary tc USING (nct_id)
WHERE t.study_type = 'INTERVENTIONAL';

-- partner pairs, one row per (trial, condition, asset, partner) — the Q7 query surface
CREATE OR REPLACE VIEW v_combo_partners AS
SELECT c.nct_id, c.condition_key, a.asset_id, p.asset_id AS partner_asset_id, c.source, c.has_background,
       c.phase_norm, c.phase_rank, c.overall_status, c.program_exists, c.is_industry
FROM v_combos c, unnest(c.asset_ids) AS a(asset_id), unnest(c.asset_ids) AS p(asset_id)
WHERE a.asset_id <> p.asset_id;

-- ---------------------------------------------------------------- v_population_landscape: Q6 rollup
CREATE OR REPLACE VIEW v_population_landscape AS
SELECT pm.kind, pm.term_id, tc.condition_key,
       count(DISTINCT pm.nct_id) AS n_trials,
       count(DISTINCT pm.nct_id) FILTER (WHERE t.program_exists) AS n_active_trials,
       list(DISTINCT pm.nct_id ORDER BY pm.nct_id)[1:10] AS example_ncts,
       list(DISTINCT pm.nct_id ORDER BY pm.nct_id) AS nct_ids
FROM population_mentions pm
JOIN v_trials t USING (nct_id)
JOIN v_trial_conditions_primary tc USING (nct_id)
WHERE t.study_type = 'INTERVENTIONAL' AND t.is_drug_trial
GROUP BY 1, 2, 3;

-- ---------------------------------------------------------------- v_trial_card: everything the trial drawer needs, one row
CREATE OR REPLACE VIEW v_trial_card AS
SELECT t.nct_id, s.brief_title, s.official_title, t.study_type, t.overall_status, t.phase_norm, t.phase_rank,
       t.program_exists, t.is_active_readout, t.is_inactive, t.is_industry,
       t.lead_company_id, c.canonical_name AS lead_company_name,
       (SELECT list(struct_pack(role := sp.role, name := sp.name_raw, agency_class := sp.agency_class))
          FROM sponsors sp WHERE sp.nct_id = t.nct_id) AS sponsors,
       (SELECT list(name_raw ORDER BY position) FROM study_conditions sc WHERE sc.nct_id = t.nct_id) AS conditions_listed,
       (SELECT list(struct_pack(condition_key := tc.condition_key, display_name := tc.display_name, source := tc.source))
          FROM v_trial_conditions_primary tc WHERE tc.nct_id = t.nct_id) AS conditions_primary,
       (SELECT list(struct_pack(mesh_id := mt.mesh_id, term := mt.term))
          FROM mesh_terms mt WHERE mt.nct_id = t.nct_id AND mt.module = 'condition' AND mt.kind = 'mesh') AS mesh_conditions,
       (SELECT list(struct_pack(arm_no := a.arm_no, label := a.label, type := a.type, description := a.description,
                                assets := (SELECT list(struct_pack(asset_id := ta.asset_id, role := ta.role, intervention := i.name_raw))
                                           FROM arm_interventions ai
                                           JOIN trial_assets ta ON ta.nct_id = ai.nct_id AND ta.intervention_no = ai.intervention_no
                                           JOIN interventions i ON i.nct_id = ai.nct_id AND i.intervention_no = ai.intervention_no
                                           WHERE ai.nct_id = a.nct_id AND ai.arm_no = a.arm_no))
                    ORDER BY a.arm_no)
          FROM arms a WHERE a.nct_id = t.nct_id) AS arms,
       (SELECT list(struct_pack(asset_id := ta.asset_id, canonical_name := aa.canonical_name, role := ta.role,
                                in_all_arms := ta.in_all_arms, intervention := i.name_raw, intervention_type := i.type))
          FROM trial_assets ta JOIN assets aa USING (asset_id)
          JOIN interventions i ON i.nct_id = ta.nct_id AND i.intervention_no = ta.intervention_no
          WHERE ta.nct_id = t.nct_id AND ta.via = 'name') AS trial_assets,
       (SELECT list(struct_pack(term_id := pm.term_id, kind := pm.kind, surface := pm.surface, evidence := pm.evidence_line))
          FROM population_mentions pm WHERE pm.nct_id = t.nct_id) AS population_mentions,
       s.eligibility_criteria, s.sex, s.minimum_age, s.maximum_age, s.std_ages, s.healthy_volunteers,
       s.enrollment_count, s.enrollment_type, s.primary_purpose, s.brief_summary,
       s.start_date, s.primary_completion_date, s.completion_date, s.last_update_date, t.date_precision, s.has_results,
       'https://clinicaltrials.gov/study/' || t.nct_id AS ctgov_url
FROM v_trials t
JOIN studies s USING (nct_id)
LEFT JOIN companies c ON c.company_id = t.lead_company_id;
