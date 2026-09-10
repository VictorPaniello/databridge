"""Ingestion pipeline: take an uploaded file, clean it via tidycsv, persist
the results, and trigger a webhook for each newly inserted record.

Note on reusing tidycsv's lower-level functions instead of its `clean()`
convenience wrapper: `clean()` returns `clean_df` with its index reset to
0..N-1 after deduplication, while `result.issues` still carries each issue's
*original* row_index from before dedup. That's fine for tidycsv's own CLI,
which only ever prints `row_index` as a label for a human to cross-reference
against their source file - it never needs to align an issue back to a
specific row of `clean_df` programmatically. This service does need that
alignment (to know which persisted record `has_issues`), so it calls
`coerce_and_validate` and `flag_duplicates` directly instead of `clean()`:
`flag_duplicates` does not reset the index, so the surviving rows keep their
original position and can be matched against `issues` directly."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from tidycsv.cleaner import coerce_and_validate, flag_duplicates, load_input, map_columns
from tidycsv.schema import Schema

from databridge.config import settings
from databridge.models import ClientRecord, IngestionRun
from databridge.webhooks import notify_new_record


def load_schema() -> Schema:
    return Schema.load(Path(settings.schema_path))


def ingest_file(
    db: Session, filename: str, content: bytes, schema: Schema, owner_id: uuid.UUID
) -> tuple[list[ClientRecord], IngestionRun]:
    suffix = Path(filename).suffix or ".csv"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        raw = load_input(tmp_path)
        mapped = map_columns(raw, schema)
        coerced, issues = coerce_and_validate(mapped, schema)
        deduped, dropped = flag_duplicates(coerced, schema.key_columns)
    finally:
        tmp_path.unlink(missing_ok=True)

    issue_map: dict[int, list[dict]] = {}
    for issue in issues:
        issue_map.setdefault(issue.row_index, []).append(
            {"field": issue.field, "issue": issue.issue}
        )

    # Created up front - rows_clean/rows_flagged are filled in below once
    # the loop that computes them finishes, but rows_total/
    # rows_dropped_duplicates are already known here, and run.id needs to
    # exist before any ClientRecord below can reference it.
    run = IngestionRun(
        owner_id=owner_id,
        source_file=filename,
        rows_total=len(raw),
        rows_clean=0,
        rows_flagged=0,
        rows_dropped_duplicates=dropped,
    )
    db.add(run)
    db.flush()  # assigns run.id

    inserted: list[ClientRecord] = []
    skipped_existing = 0
    for idx in deduped.index:
        # Column-wise .at[] access, not deduped.iterrows(): iterrows() builds
        # a fresh per-row Series spanning every column, and pandas infers a
        # single dtype for that Series just like it does for a column - so a
        # correctly-None cell comes back as float NaN yet again once it's
        # inside a row-Series, even though the source column holds it fine.
        # Same root cause as the fix upstream in tidycsv, different call site.
        row_issues = issue_map.get(idx)
        email = deduped.at[idx, "email"]
        if email:
            # Scoped to this owner: two different engineers uploading a
            # client with the same email are two separate records, not a
            # duplicate of each other's - each engineer's dedup is their own.
            existing = db.execute(
                select(ClientRecord).where(
                    ClientRecord.email == email, ClientRecord.owner_id == owner_id
                )
            ).scalar_one_or_none()
            if existing:
                # Already ingested - re-uploading the same list is a no-op,
                # not an error. Counted, not just skipped silently: without
                # this, rows_total stops summing to rows_clean +
                # rows_flagged + rows_dropped_duplicates on a re-upload,
                # and the run's own numbers no longer account for every row.
                skipped_existing += 1
                continue

        record = ClientRecord(
            owner_id=owner_id,
            ingestion_run_id=run.id,
            source_file=filename,
            full_name=deduped.at[idx, "full_name"],
            email=email,
            signup_date=deduped.at[idx, "signup_date"],
            amount=deduped.at[idx, "amount"],
            phone=deduped.at[idx, "phone"],
            has_issues=row_issues is not None,
            issues=row_issues,
        )
        db.add(record)
        db.flush()  # assigns record.id before we reference it in the webhook
        inserted.append(record)

    run.rows_flagged = sum(1 for r in inserted if r.has_issues)
    run.rows_clean = len(inserted) - run.rows_flagged
    run.rows_skipped_existing = skipped_existing

    db.commit()

    for record in inserted:
        notify_new_record(db, record)

    return inserted, run
