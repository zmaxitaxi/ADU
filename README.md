# ADU permit classifier

Identify accessory dwelling unit (ADU) projects from building-permit descriptions
and classify their relationship to the primary dwelling. The Python pipeline
appends exactly two columns while preserving every original cell, identifier,
and row position:

| Column | Values |
| --- | --- |
| ADU project | Yes or No |
| ADU type | Attached, Detached, or Unclear for Yes; blank for No |

Current version: **1.1.0**. Requires **Python 3.9 or later**, standard library
only. Permit text is treated as data and never executed.

## Quick start

```sh
git clone https://github.com/zmaxitaxi/ADU.git
cd ADU
python3 run_checks.py --unit-only
```

Bring your own CSV or gzip CSV containing a `DESCRIPTION` column. Input fields
are copied as strings, preserving leading zeros. Use new output paths; existing
files are protected from overwrite.

```sh
mkdir -p deliverables
python3 classify_adu.py INPUT.csv.gz deliverables/labeled.csv.gz \
  --audit deliverables/evidence.csv.gz --summary deliverables/summary.json
python3 verify_output.py INPUT.csv.gz deliverables/labeled.csv.gz \
  deliverables/summary.json --audit deliverables/evidence.csv.gz \
  --reclassify --receipt deliverables/verification.json
```

The evidence sidecar contains one row per permit with labels, normalized evidence,
reasons, and review flags. Its `source_row` is the CSV record number including
the header, not a physical line number for multiline descriptions. The summary
records counts, source profiles, classifier version, and SHA-256 hashes.

Source datasets, labeled exports, review samples, generated reports, and PDFs
are excluded from Git and are not downloadable here. Software tests use embedded
fixtures and temporary files.

## Classification policy

Only `DESCRIPTION` supplies label evidence. `TYPE`, `SUBTYPE`, `PROJECTNAME`,
`STATUS`, and property identifiers do not override the labels.

**Yes** requires affirmative ADU involvement. New construction, conversion,
legalization, demolition, repairs, utilities, and direct ancillary or
administrative work count. Recognized terms include ADU/JADU plurals, dotted or
spaced acronyms, accessory dwelling with or without “unit,” source spelling
variants, and compact measurements. DADU means detached ADU. In-law
units/quarters, granny flats, secondary dwellings/units, and second dwellings
are provisional aliases flagged for review.

**No** means no affirmative evidence under the rules. Explicit removal from
current scope overrides historical scope. Generic eligibility boilerplate,
shared-plan identifiers alone, and passing ADU references under separate permits
are excluded. Direct work such as grading for an ADU still counts when its
building permit is separate. Unresolved references are flagged.

Guest houses, casitas, ordinary second units, duplexes, and garage conversions
without recognized ADU evidence are conservatively No and flagged. Blank
descriptions are also No, with a `missing_description` flag. These labels are
not verified negative examples or proof an ADU is absent.

**Attached/Detached** refers to the primary dwelling. Explicit unit adjectives,
conversion of an attached/detached garage, and stated connection to the primary
dwelling may establish type. An ADU's own attached garage, shared utilities,
meters, and attached plans do not establish physical attachment.

**Unclear** applies to insufficient/conflicting evidence, mixed Attached and
Detached units, or multiple recognized units with incomplete type evidence.
JADU alone does not establish Attached. Repeated mentions do not automatically
mean separate units. Complex descriptions may require human adjudication.

## Tests and file verification

```sh
# Software tests, requiring no permit dataset; used by GitHub Actions.
python3 run_checks.py --unit-only

# Full verification and fresh-export reproducibility with your own files.
python3 run_checks.py --source INPUT.csv.gz \
  --output deliverables/labeled.csv.gz --audit deliverables/evidence.csv.gz \
  --summary deliverables/summary.json --report reports/test_report.json
```

The runner exits nonzero on failure. It may refresh its generated test report;
source and delivered data are not rewritten. Full mode without explicit paths
expects the local September export under `deliverables/v1_1/` and reads its
source path from the summary. These files are absent from a fresh clone, so use
`--unit-only` or supply all data paths.

The 63 test methods cover source-based regressions, negation, scope references,
unrelated attachment language, deterministic inputs, Unicode/multiline cells,
leading zeros, invalid CSV/encoding/gzip, empty inputs, failure cleanup,
overwrite protection, deliberate output corruption, and validation under
Python's `-O` option. Sampling/evaluation tests check blinding, reproducibility,
metric arithmetic, uncertainty exclusions, and invalid reviews.

Integrity tests establish software and file contracts, not semantic accuracy.

## Diagnostic evaluation

```sh
python3 prepare_evaluation.py INPUT.csv.gz reports/new_review
python3 evaluate_labels.py reports/new_review/predictions.csv REVIEW_A.csv \
  --review-b REVIEW_B.csv --report reports/new_metrics.json
```

The sampler uses four strata: predicted Yes, metadata-candidate No,
ambiguous-description No, and other No. It avoids duplicate normalized
descriptions and nonblank property IDs. Previously reviewed permits, their
nonblank properties, and matching descriptions are excluded when local review
records exist. Historical review JSON files are not distributed; a fresh clone
only excludes regression-test IDs unless those records are restored locally.

Review descriptions without viewing predictions. Enter `review_project` as
Yes/No/Uncertain, `review_type` as Attached/Detached/Unclear for Yes and blank
otherwise, and a reason in `review_notes`. The evaluator requires matching IDs
and valid, reasoned labels. Project uncertainty and unresolved type disagreements
are explicitly excluded. Freeze rules before comparison. Enriched diagnostic
sample metrics are unweighted and are not population accuracy estimates.

The October 9 review used 80 descriptions and two prediction-blinded AI reviewers.
They agreed on 20 Yes, 57 No, and three Uncertain. Frozen predictions had TP=20,
FP=0, FN=0, TN=57 on 77 scorable records; type agreement was 19/20. Both reviewers
participated in earlier development, with some historical source exposure. This
is not human ground truth or a fully independent holdout. One interior conversion
was judged Attached by reviewers but remains conservatively Unclear in the code;
the contextual inference requires adjudication. Earlier reviews informed rules
and are not an unbiased accuracy benchmark. Obtain human labels before claiming
production accuracy.

## Metadata audit and later GIS work

```sh
python3 audit_specifications.py INPUT.csv.gz CURRENT.csv.gz PREVIOUS.csv.gz \
  reports/new_audit
```

The audit writes label changes, metadata candidates, linkage counts and input
hashes to a new directory. It requires unique `BUILDINGPERMITID` values, matching
exports and source fields `ATTOMID`, `DESCRIPTION`, `TYPE`, `SUBTYPE`,
`PROJECTNAME`. Broad metadata categories may mention ADU alongside unrelated
work; candidates are not confirmed ADUs.

The September source contained 73,587 rows. Version 1.1.0 labels 2,864 Yes and
70,723 No; Yes types are 404 Attached, 924 Detached, and 1,536 Unclear. Full local
checks preserved 2,869,893 original cells and reproduced CSV/audit content. Two
passing-reference records changed from the original export. There were 3,912
blank descriptions and 520 metadata candidates among No labels.

The source includes mixed permit stages and effective dates through September 23;
it does not establish an exclusively approved or complete-month cohort. There
are 13,945 blank ATTOMIDs, including 572 Yes permits. The 2,292 Yes permits with
nonblank ATTOMID span 1,806 provider property IDs, not a validated parcel join.
Multiple permits may concern one property or dwelling.

Before imagery work, verify parcel joins, completion and image dates; define an
image-level target and keep repeated properties together across training and
evaluation splits. Repairs, demolition, JADUs and interior conversions require
different handling from visible new structures. A No label or absence of permits
does not establish an ADU-free image.

Provider references: [ATTOM API documentation](https://api.developer.attomdata.com/docs)
and [permit linkage guidance](https://cloud-help.attomdata.com/article/494-building-permits).
The task's linked [Dewey dictionary](https://app.deweydata.io/data/attom-data/building-permits)
was inaccessible during review; its exact definitions remain unverified.
