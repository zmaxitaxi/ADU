# ADU action policy, version 1.2.0

ADU action describes the **current permit's purpose for the ADU** from DESCRIPTION.
It is blank for ADU project No. Positive records without sufficient purpose
evidence receive Unclear. Action is separate from physical type, so an Attached
unit can have any action and an Unclear type can still have a clear purpose.

| Action | Operational definition |
| --- | --- |
| New construction | Explicit construction or placement of an ADU, including prefabricated units. |
| Conversion | Explicit change of existing space or another use into an ADU. |
| Addition | Explicit floor-area expansion serving the ADU. |
| Legalization | Current regularization of an already existing ADU or conversion. |
| Repair/remodel | Repair, renovation, reroofing, internal room alterations or partial removal. |
| Demolition/removal | Explicit demolition/removal of an ADU or reversal of its ADU use. |
| Reconstruction | Explicit rebuild or replacement, including a linked ADU demolition/replacement. |
| Utility work | Standalone trade, equipment or service work for the ADU. |
| Site preparation | Grading, drainage, access/site work or non-ADU demolition serving an ADU. |
| Administrative | Review, revision, renewal, completion/certificate or address work. |
| Unclear | ADU evidence exists, but purpose/pathway is insufficient or unresolved. |

These are operational text categories, not legal determinations. This is a
conservative initial taxonomy for discussion with the project lead.

## Several purposes

Canonical labels retain explicit actions separated by semicolon and space:

- `Conversion; Addition`: conversion plus added floor area.
- `Demolition/removal; Reconstruction`: removal and linked rebuild.
- `New construction; Conversion`: construction of one unit and conversion for another.

`action_components(label)` validates order, spelling and uniqueness. Unclear is
used alone. Tags express supported purposes, not counts of units. Unscoped unit
mentions receive a review flag rather than an invented extra action. Repeated
mentions can describe the same unit, so they do not automatically imply mixed work.

## Scope rules and examples

| Description | ADU action | Reason |
| --- | --- | --- |
| Construct detached ADU | New construction | Explicit building work. |
| New ADU | Unclear | New unit could mean new construction or conversion. |
| Convert garage to new ADU | Conversion | Conversion controls over the adjective new. |
| Convert garage to ADU with a 200 sf addition | Conversion; Addition | Both changes are explicit. |
| Legalize existing garage conversion to ADU | Legalization | Existing conversion is the object being regularized. |
| Replace water heater in existing ADU | Repair/remodel | Direct replacement work in the unit. |
| Plumbing for construction of ADU | Utility work | The current permit is plumbing. |
| Install solar panels for ADU | Utility work | Equipment installation does not establish unit construction. |
| Demolish garage for future ADU | Site preparation | Target is a garage, not an existing ADU. |
| Remove sprinkler from ADU | Utility work | Removing a component does not demolish the unit. |
| Demolish unpermitted JADU and convert back to garage | Demolition/removal | ADU use is removed even if the shell remains. |
| Minor revision to remove sprinkler requirement for ADU | Administrative | A plan requirement changes. |
| Completion permit referring to new ADU construction | Administrative | The wrapper describes the current purpose. |
| Legalize main house. Convert garage to ADU | Conversion | Legalization belongs to another object. |

Utility/site/administrative wrappers identify permit purpose even when the
description quotes the larger ADU project. Combined building work does not gain
a Utility tag merely because equipment appears as part of the same unit's
construction. Scope heuristics still require review on complex descriptions.
Administrative categorization does not prove that no corrective physical work
will occur.

The action classifier masks recognized passing references, uses affirmative
unit mentions, checks local objects and clauses, and excludes recognized
historical/negated or quoted statutory language. It uses manually specified,
bounded patterns. It cannot parse every grammatical relationship, local
abbreviation, repeated mention or mixed/historical project description.

## Audit and verification

The main output appends ADU project, ADU type and ADU action to the original
columns. The evidence sidecar retains the previous fields and adds ADU action,
action_reason, action_evidence and action_flags. Evidence is normalized text.

Summary fields:

- `adu_actions`: mutually exclusive complete-label counts among Yes permits.
- `adu_action_categories`: overlapping counts for each individual action.
- `action_review_flags`: counts of purpose-review flags.
- `action_categories`: the permitted component domain and canonical order.

Tests cover scope/object mistakes, wrappers, mixed purposes, use reversals,
partial removal, equipment, negation, historical/quoted context and corrupted
action exports. Full-file verification checks original cells, existing labels,
action labels/evidence, summary counts and reproducible CSV/audit content.

The initial source cases informed rule development. AI reviewers inspected
construction/removal implications during development. Neither these reviews nor
the tests establish independent human action accuracy. The earlier 80-record
diagnostic evaluates project/type only. A new human-labeled action evaluation is
still needed before treating the purpose labels as ground truth.

## Imagery interpretation

Actions describe intended or documented work. They do not establish when work
occurred, completion, physical visibility or net changes in unit counts.

Conversion can be internal. Legalization may substantially postdate construction.
Repair and solar work can change appearance without creating a unit. Rebuild
may replace a lost unit. Demolition/removal can reverse ADU use while retaining
the same roof. Site preparation and administrative permits can precede physical
construction or reference work under another permit.

Use action and its evidence/flags with parcel joins, completion information and
image dates. A new-construction label alone is insufficient to train a reliable
image-level positive, and No/Unclear records are not verified image negatives.
