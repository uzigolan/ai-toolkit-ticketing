# Eval cases

**Contents:** [Why this exists](#why-this-exists) ·
[Promoting a ticket](#promoting-a-ticket) ·
[The exported file](#the-exported-file) ·
[Field reference](#field-reference) ·
[What makes a good case](#what-makes-a-good-case) ·
[Using it in the toolkit repo](#using-it-in-the-toolkit-repo) ·
[Automating the copy](#automating-the-copy) ·
[Why promotion stays manual](#why-promotion-stays-manual)

## Why this exists

A ticketing app that only closes tickets teaches nobody anything. The point of
this one is the last step: a confirmed, verified ticket becomes a permanent
regression case in the toolkit's own test suite, so the toolkit cannot silently
regress on something a real employee actually hit.

That is also why the submit form keeps **the prompt** as its own field and why
a `fixed` resolution demands **the corrected answer**. Together they are a
ready-made `prompt` / `expected_answer` pair, rather than a prose description
of a bug that somebody would have to translate into a test.

## Promoting a ticket

An admin opens the ticket and clicks **Promote to eval case**. That writes a
JSON file into `exports/` and flashes the path.

Promotion is deliberately orthogonal to the workflow: it stamps `promoted_at`
and **does not change the status**. A ticket can be promoted before or after
verification, and promoting one twice just rewrites the file.

The natural moment is after the submitter has verified a `fixed` resolution —
that is when the ticket carries a prompt, a version and an answer that somebody
other than the fixer has confirmed.

## The exported file

One file per ticket, named after the case id:

```
exports/ticket-42-9f3c1a77b2d4e5c6.json
```

The id is `ticket-<id>-<signature>`, so it is stable across re-exports of the
same ticket and different for a ticket whose content has been edited.

```json
{
  "case_id": "ticket-42-9f3c1a77b2d4e5c6",
  "source": "ticketing_app",
  "source_version": "1.0.0",
  "toolkit": "rad-agent-toolkit",
  "categories": ["wrong_result"],
  "knowledge_sources": ["manual", "cli"],
  "knowledge_scope": ["rad"],
  "operations": ["lookup"],
  "title": "ETX-2 QoS shaper limits quoted from the wrong manual revision",
  "prompt": "what is the maximum shaper rate on an ETX-2 GbE port?",
  "expected_answer": "…the corrected answer, pasted by the admin…",
  "fixed_in_versions": "…rad agent show system versions output…",
  "resolution": "fixed",
  "verified_by": "alice",
  "follow_ups": [
    {"author": "alice", "at": "2026-08-20 09:12:03", "body": "…"}
  ]
}
```

## Field reference

| Field | Source |
| --- | --- |
| `case_id` | `ticket-<id>-<signature>` |
| `source` | Always `ticketing_app` |
| `source_version` | `version.APP_VERSION` — which build produced the case |
| `toolkit` | Which toolkit's suite this belongs in |
| `categories`, `knowledge_sources`, `knowledge_scope`, `operations` | The multi-selects, split back into lists |
| `title`, `description` | As filed |
| `prompt` | **The input to replay** |
| `expected_answer` | The `fixed_answer` the admin pasted after re-running the prompt |
| `suggestion` | What the submitter thought the answer should have been |
| `expected_behavior`, `actual_behavior` | Optional advanced fields |
| `transcript` | The original pasted chat, for context |
| `toolkit_version_reported` | The version that produced the bad answer |
| `resolution`, `resolution_note` | The admin's outcome |
| `fixed_in_versions` | The build carrying the fix |
| `submitted_by`, `submitted_at`, `verified_by` | Provenance |
| `follow_ups` | Every comment, in order |

`prompt` and `expected_answer` are the pair an eval runner needs; everything
else is provenance that makes a failing case explicable a year later.

## What makes a good case

- **A prompt that stands alone.** If it only makes sense after three earlier
  turns, the replay won't reproduce anything. Prefer tickets whose prompt is
  self-contained, or edit the prompt into one before promoting.
- **An answer that is checkable.** A corrected answer full of live device
  output is not a stable expectation; one that quotes a documented limit is.
- **A failure the toolkit can actually be responsible for.** A ticket about a
  device being unreachable isn't a knowledge regression.
- **Not a duplicate.** Promote the original that the duplicates collapsed onto.

## Using it in the toolkit repo

The file is a *stub*, shaped like a case in the toolkit's
`tests/evals/cases/` — not a drop-in for whatever assertion format that suite
uses. The intended flow:

1. Promote the ticket and take the file from `exports/`.
2. Copy it into the toolkit repo's cases directory.
3. Adjust it to the suite's schema — usually trimming provenance and choosing
   how `expected_answer` is compared (exact, contains, or a rubric).
4. Commit it with a reference to the ticket id, so the case's origin stays
   findable.

## Automating the copy

`exports/` is a plain directory of JSON, so anything can consume it. Options,
cheapest first:

- A scheduled job that copies new files into a branch of the toolkit repo and
  opens a pull request — review stays where reviews belong.
- A small script in the toolkit repo that reads `exports/` over a share.
- Replacing `db.export_as_eval_case()` with a direct call into
  rad-agent-toolkit's `scripts/feedback_collector.py`, if you'd rather have one
  pipeline than two.

Whatever you choose, keep a human in front of the merge.

## Why promotion stays manual

Nothing is exported until an admin clicks the button. A wrong or malicious
ticket becoming a permanent eval case unreviewed would be worse than a slow
queue: it would make the suite enforce an answer that is itself wrong, and
every future contributor would treat that as the specification.

If you eventually trust the signature dedup enough, auto-promoting anything
with several independent duplicate reports is a reasonable next step — the
duplicates are the corroboration.
