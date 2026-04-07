# KMP Repair Pipeline — Evaluation Report

| case_id | repair_mode | case_status | repo_url | pr_ref | update_class | bsr | ctsr | ffsr | efr | hit_at_1 | hit_at_3 | hit_at_5 | source_set_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3407b237-981f-40da-9623-4c4ac3c2087b | iterative_agentic | EVALUATED | https://github.com/estebancastelblanco/kmp-production-sample-impact-demo | pull/1 | direct_library | 1.000 | 1.000 | 1.000 | 1.000 | N/A | N/A | N/A | N/A |

## Per-Mode Averages

| mode | n | bsr | ctsr | ffsr | efr | hit_at_1 | hit_at_3 | hit_at_5 | source_set_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| iterative_agentic | 1 | 1.000 | 1.000 | 1.000 | 1.000 | N/A | N/A | N/A | N/A |

## Attempt Strategy Comparison

| case_id | repair_mode | attempt_number | patch_strategy | patch_status | validation_status | created_at |
| --- | --- | --- | --- | --- | --- | --- |
| 3407b237-981f-40da-9623-4c4ac3c2087b | iterative_agentic | 1 | single_diff | VALIDATED | SUCCESS_REPOSITORY_LEVEL | 2026-04-07 20:09:24.115574+00:00 |
| 3407b237-981f-40da-9623-4c4ac3c2087b | iterative_agentic | 2 | single_diff | VALIDATED | SUCCESS_REPOSITORY_LEVEL | 2026-04-07 20:59:10.527767+00:00 |