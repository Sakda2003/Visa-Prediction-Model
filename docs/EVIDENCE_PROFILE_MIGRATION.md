# Evidence profile migration

The evidence-v3 integration adds profile and assessment fields. It does not need a destructive schema migration. Existing applications retain their old policy and diagnostic results until staff explicitly reassess them.

The draft-only helper copies current active requirements into a new draft per visa type. It never normalizes an old budget into 100, publishes a policy, imports documents, trains a model, or changes historical application scores. Invalid budgets are reported as drafts needing Admin revision.

Run from the project root with the configured MongoDB environment:

```powershell
.\.venv\Scripts\python.exe backend\scripts\migrate_evidence_profiles.py --run-id agency-upgrade-1
```

The default is a read-only dry run. Review its counts and use the same run ID with `--apply` to create drafts. Repeating that run is idempotent. In Requirements Management, inspect each draft's allocations, scorer versions, alternatives, sources, and effective dates; publish a valid 100-point release with a reason. Publication uses a compare-and-swap on the reviewed draft revision and inserts one immutable release. Published versions have stable IDs and content hashes.

Existing cases do not adopt the new policy automatically. Staff choose an effective published version through explicit reassessment. That creates an assessment-history entry, preserves the previous policy reference, and invalidates any old prediction. Case creation automatically snapshots an effective published profile when one exists.

Rollback preview:

```powershell
.\.venv\Scripts\python.exe backend\scripts\migrate_evidence_profiles.py --rollback-run agency-upgrade-1
```

Add `--apply` only to remove unchanged, still-unpublished drafts created by that run. Edited drafts, draft revisions advanced by publication, published profiles, and drafts referenced by a published profile are refused. Rollback never removes customer documents, assessments, models, or applications. Back up the database before an operator-initiated migration or rollback.
