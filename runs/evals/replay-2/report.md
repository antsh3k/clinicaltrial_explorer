# eval report — replay 20260904-161011

**passed:** True · **objective:** 0.9818 · **FLOOR breaches:** none

| case | archetype | check | score | adjudicated | borderline |
|---|---|---|---|---|---|
| G01 | Q1 | entity_set | 1.0 | False | False |
| G02 | Q2 | top_k_contains | 1.0 | False | False |
| G03 | Q3 | top_k_contains | 1.0 | False | False |
| G05 | Q5 | nct_set | 1.0 | False | False |
| G06 | Q6 | contains_all | 0.8 | False | False |
| G07 | Q7 | contains_all | 1.0 | False | False |
| G08 | negative | honest_empty | 1.0 | True | False |
| G09 | negative | refuse_approval | 1.0 | True | False |
| G10 | messiness | role_split | 1.0 | True | False |
| G11 | messiness | reconcile | 1.0 | True | False |
| G12 | messiness | states_rollup | 1.0 | True | False |
| G05b | Q5 | nct_set | 1.0 | False | True |
| G03b | Q3 | top_k_contains | 1.0 | False | True |

| metric | role | value | denominator | section |
|---|---|---|---|---|
| case_score | OBJ | 1.0 |  | case:G01 |
| case_score | OBJ | 1.0 |  | case:G02 |
| case_score | OBJ | 1.0 |  | case:G03 |
| case_score | OBJ | 1.0 |  | case:G05 |
| case_score | OBJ | 0.8 |  | case:G06 |
| case_score | OBJ | 1.0 |  | case:G07 |
| case_score | OBJ | 1.0 |  | case:G08 |
| case_score | OBJ | 1.0 |  | case:G09 |
| case_score | OBJ | 1.0 |  | case:G10 |
| case_score | OBJ | 1.0 |  | case:G11 |
| case_score | OBJ | 1.0 |  | case:G12 |
| case_score | DIAG | 1.0 |  | case:G05b |
| case_score | DIAG | 1.0 |  | case:G03b |
| unadjudicated_set_cases | DIAG | 3.0 |  | pooled |
| replay_mismatch_count | FLOOR | 0.0 |  | replay |
| total_input_tokens | DIAG | 5400.0 |  | usage |
| total_output_tokens | DIAG | 13306.0 |  | usage |
| mean_latency_ms | DIAG | 198.0 |  | usage |
| pct_answers_touching_2plus_views | DIAG | 53.8 |  | usage |
