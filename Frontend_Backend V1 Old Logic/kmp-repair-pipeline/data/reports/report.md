# KMP Repair Pipeline — Evaluation Report

| case_id | repair_mode | case_status | repo_url | pr_ref | update_class | bsr | ctsr | ffsr | efr | hit_at_1 | hit_at_3 | hit_at_5 | source_set_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3407b237-981f-40da-9623-4c4ac3c2087b | context_rich | EVALUATED | https://github.com/estebancastelblanco/kmp-production-sample-impact-demo | pull/1 | direct_library | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |
| 3407b237-981f-40da-9623-4c4ac3c2087b | full_thesis | EVALUATED | https://github.com/estebancastelblanco/kmp-production-sample-impact-demo | pull/1 | direct_library | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |
| 3407b237-981f-40da-9623-4c4ac3c2087b | iterative_agentic | EVALUATED | https://github.com/estebancastelblanco/kmp-production-sample-impact-demo | pull/1 | direct_library | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |
| 3407b237-981f-40da-9623-4c4ac3c2087b | raw_error | EVALUATED | https://github.com/estebancastelblanco/kmp-production-sample-impact-demo | pull/1 | direct_library | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |

## Per-Mode Averages

| mode | n | bsr | ctsr | ffsr | efr | hit_at_1 | hit_at_3 | hit_at_5 | source_set_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| context_rich | 1 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |
| full_thesis | 1 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |
| iterative_agentic | 1 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |
| raw_error | 1 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A | N/A |

## Attempt Strategy Comparison

| case_id | repair_mode | attempt_number | patch_strategy | patch_status | validation_status | created_at |
| --- | --- | --- | --- | --- | --- | --- |
| 3407b237-981f-40da-9623-4c4ac3c2087b | context_rich | 1 | single_diff | REJECTED | FAILED_BUILD | 2026-04-05 16:42:54.332752+00:00 |
| 3407b237-981f-40da-9623-4c4ac3c2087b | full_thesis | 1 | single_diff | REJECTED | FAILED_BUILD | 2026-04-05 16:43:02.293674+00:00 |
| 3407b237-981f-40da-9623-4c4ac3c2087b | full_thesis | 2 | single_diff | REJECTED | FAILED_BUILD | 2026-04-05 19:05:08.698865+00:00 |
| 3407b237-981f-40da-9623-4c4ac3c2087b | iterative_agentic | 1 | single_diff | REJECTED | FAILED_BUILD | 2026-04-05 16:42:57.925611+00:00 |
| 3407b237-981f-40da-9623-4c4ac3c2087b | raw_error | 1 | single_diff | FAILED_APPLY | NOT_RUN | 2026-04-05 16:42:50.068660+00:00 |