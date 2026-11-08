# Tool contract

The authenticated `/mcp` endpoint provides exactly nine tools. Every successful response has structured JSON and a short Chinese text summary. Business errors set `isError` with a stable `code`; invalid authentication uses HTTP 401, disabled users or untrusted Origin use HTTP 403.

| Tool | Inputs | Result |
|---|---|---|
| list_banks | bank_id?, include_papers? | Authorized banks, counts, papers, filters, answer coverage |
| find_questions | banks? or bank+paper+numbers, filters?, cursor? | Deterministic complete set, paged display, frozen selection_id |
| search_questions | query, banks?, filters?, top_k<=20 | Related candidates; always coverage=partial |
| get_question | ref, image_offset? | Server-side full text, up to two verified resized images |
| review_topic | banks+topic+filters, then review_id+decisions | Every scoped question reviewed; selection_id only at completion |
| create_worksheet | title, exactly one of filters/items/selection_id, answers?, seed?, request_id?, based_on_job?, wait_seconds<=45 | queued/running/succeeded/empty/failed and owned file links |
| get_job | job_id?, wait_seconds? | Owned job or recent owned jobs |
| cancel_job | job_id | Cancellation of owned queued/running work |
| get_files | job_id, kinds? | Fresh short-lived links after disk SHA verification |

Filters support banks, paper (unique substring), original numbers, question_type, choice_mode, topics, topic_match, exclude_refs, reviewed_selection and count. `any` intersects tag sets; `all` contains all requested tags and permits additional tags; `exact` requires identical tag sets. Subtype filters require a validated paper-structure profile. Sampling is reproducible for the same seed.

Items accept only `{ref}` or `{bank,paper,number}`. Model-authored content or answers are rejected. Explicit duplicate source questions are rejected; deterministic cross-bank selection deduplicates by source-document SHA plus original number. A count shortfall preserves the original conditions and reports requested, actual and missing counts. Zero matches create neither task nor PDF.

Application jobs survive client disconnection. Use polling; only cancel_job cancels. Same request identity is reused for 24 hours in queued/running/succeeded states. Failed or cancelled jobs can be retried. Changing a request while reusing a request_id returns idempotency_conflict.

No management tools are registered. Search quality, AI tags, physics correctness, teacher review, real-client compatibility and learner outcomes require separate evidence.
