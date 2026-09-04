# eval report — live 20260904-182845

**passed:** False · **objective:** 0.8346 · **FLOOR breaches:** ['hard_failure_count'] (cases G03)

| case | archetype | check | score | adjudicated | borderline |
|---|---|---|---|---|---|
| G01 | Q1 | entity_set | 1.0 | True | False |
| G02 | Q2 | top_k_contains | 1.0 | True | False |
| G03 | Q3 | top_k_contains | 0.0 | True | False |
| G04 | Q4 | contains_all | 0.3333 | True | False |
| G05 | Q5 | nct_set | 1.0 | True | False |
| G06 | Q6 | contains_all | 1.0 | True | False |
| G07 | Q7 | contains_all | 1.0 | True | False |
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
| hard_failure_count | FLOOR | 1.0 |  | case:G03 |
| case_score | OBJ | 0.0 |  | case:G03 |
| case_score | OBJ | 0.3333 |  | case:G04 |
| case_score | OBJ | 1.0 |  | case:G05 |
| case_score | OBJ | 1.0 |  | case:G06 |
| case_score | OBJ | 1.0 |  | case:G07 |
| case_score | OBJ | 1.0 |  | case:G08 |
| case_score | OBJ | 1.0 |  | case:G09 |
| case_score | OBJ | 1.0 |  | case:G10 |
| case_score | OBJ | 1.0 |  | case:G11 |
| case_score | OBJ | 1.0 |  | case:G12 |
| case_score | DIAG | 1.0 |  | case:G05b |
| case_score | DIAG | 1.0 |  | case:G03b |
| nct_set_precision | OBJ | 1.0 | 34.0 | pooled |
| nct_set_recall | OBJ | 0.3505 | 97.0 | pooled |
| nct_set_f1 | DIAG | 0.5191 | 97.0 | pooled |
| entity_set_precision | DIAG | 0.2 | 25.0 | pooled |
| entity_set_recall | DIAG | 0.8333 | 6.0 | pooled |
| entity_set_f1 | DIAG | 0.3226 | 6.0 | pooled |
| unadjudicated_set_cases | DIAG | 1.0 |  | pooled |
| replay_mismatch_count | DIAG | 0.0 |  | replay |
| total_input_tokens | DIAG | 794280.0 |  | usage |
| total_output_tokens | DIAG | 44068.0 |  | usage |
| mean_latency_ms | DIAG | 36627.0 |  | usage |
| pct_answers_touching_2plus_views | DIAG | 64.3 |  | usage |
