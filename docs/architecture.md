# Architecture

Bearer middleware derives the principal and grants from hashed server-side credentials. Catalog, reviews, frozen selections, jobs, files and download capabilities carry ownership; parameters cannot choose the principal. SQLite WAL and immediate transactions serialize queue claims, quotas, cancellation and publication.

Bank releases contain unchanged original question packages, source-bound tags and review selections, optional original-answer packages, a release manifest and a complete file checksum manifest. Import performs legacy revision verification and raw-text reconciliation. Workers load the job-bound release, never the current pointer, and materialize verified assets before handing source-only ExamSpec to the fixed renderer.

The API submits work and polls for at most 45 seconds. A separate single worker supervises one child process per task and renews its lease. Expired leases recover once, then fail visibly. Cancellation kills the process group; failed artifacts and logs remain private. Successful artifacts are registered only after rendering and PDF QA. Fresh download handles require file SHA verification; permanent API-token downloads additionally require ownership.

Immutable bank data and private runtime data do not enter the repository. Administrative capabilities are shell-only. Search indices are derived data, not an alternative source of question content. A complete deterministic set or completed model review establishes scope coverage only; neither establishes scientific correctness or teaching outcomes.
