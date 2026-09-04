# eval report — live 20260904-154424

**passed:** False · **objective:** 0.6667 · **FLOOR breaches:** ['hard_failure_count', 'hard_failure_count', 'hard_failure_count'] (cases G05, G05b, G06, G08)

| case | archetype | check | score | adjudicated | borderline |
|---|---|---|---|---|---|
| G01 | Q1 | entity_set | 1.0 | False | False |
| G02 | Q2 | top_k_contains | 1.0 | False | False |
| G03 | Q3 | top_k_contains | 1.0 | False | False |
| G04 | Q4 | contains_all | 0.0 | False | False |
| G05 | Q5 | nct_set | 0.0 | False | False |
| G06 | Q6 | contains_all | 0.0 | False | False |
| G07 | Q7 | contains_all | 1.0 | False | False |
| G08 | negative | honest_empty | 0.0 | True | False |
| G09 | negative | refuse_approval | 1.0 | True | False |
| G10 | messiness | role_split | 1.0 | True | False |
| G11 | messiness | reconcile | 1.0 | True | False |
| G12 | messiness | states_rollup | 1.0 | True | False |
| G05b | Q5 | nct_set | 0.0 | False | True |
| G03b | Q3 | top_k_contains | 1.0 | False | True |

| metric | role | value | denominator | section |
|---|---|---|---|---|
| case_score | OBJ | 1.0 |  | case:G01 |
| case_score | OBJ | 1.0 |  | case:G02 |
| case_score | OBJ | 1.0 |  | case:G03 |
| case_score | OBJ | 0.0 |  | case:G04 |
| hard_failure_count | FLOOR | 1.0 |  | case:G05 |
| case_score | OBJ | 0.0 |  | case:G05 |
| hard_failure_count | FLOOR | 1.0 |  | case:G06 |
| case_score | OBJ | 0.0 |  | case:G06 |
| case_score | OBJ | 1.0 |  | case:G07 |
| hard_failure_count | FLOOR | 1.0 |  | case:G08 |
| case_score | OBJ | 0.0 |  | case:G08 |
| case_score | OBJ | 1.0 |  | case:G09 |
| case_score | OBJ | 1.0 |  | case:G10 |
| case_score | OBJ | 1.0 |  | case:G11 |
| case_score | OBJ | 1.0 |  | case:G12 |
| hard_failure_count | FLOOR | 1.0 |  | case:G05b |
| case_score | DIAG | 0.0 |  | case:G05b |
| case_score | DIAG | 1.0 |  | case:G03b |
| unadjudicated_set_cases | DIAG | 1.0 |  | pooled |
| replay_mismatch_count | DIAG | 0.0 |  | replay |
| total_input_tokens | DIAG | 1200804.0 |  | usage |
| total_output_tokens | DIAG | 44134.0 |  | usage |
| mean_latency_ms | DIAG | 55834.0 |  | usage |
| pct_answers_touching_2plus_views | DIAG | 85.7 |  | usage |
