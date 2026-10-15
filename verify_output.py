#!/usr/bin/env python3
"""Validate saved values, labels, summary, and optional audit evidence.

Checks remain active under Python -O. Input files are read-only.
"""
import argparse
import collections
import csv
import itertools
import json
from contextlib import ExitStack
from pathlib import Path

from classify_adu import (ACTION_CATEGORIES, LABEL_FIELDS, action_components,
                          classify_action, classify_description, open_csv, sha256)

AUDIT_FIELDS = ["source_row", "BUILDINGPERMITID", "DESCRIPTION", "ADU project",
                "ADU type", "reason", "evidence", "review_flags", "ADU action",
                "action_reason", "action_evidence", "action_flags"]


class VerificationError(ValueError):
    """A saved output or its evidence violates the export contract."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def verify(source_path, output_path, summary_path, audit_path=None, reclassify=False):
    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    require(isinstance(summary, dict), "Summary must be a JSON object")
    counts, types, flags = collections.Counter(), collections.Counter(), collections.Counter()
    actions, components, action_flags = collections.Counter(), collections.Counter(), collections.Counter()
    statuses, dates, states = collections.Counter(), collections.Counter(), collections.Counter()
    rows = missing = duplicate_ids = 0
    identifiers = set()
    with ExitStack() as stack:
        original = stack.enter_context(open_csv(source_path, "r"))
        saved = stack.enter_context(open_csv(output_path, "r"))
        source, output = csv.reader(original, strict=True), csv.reader(saved, strict=True)
        fields = next(source, None)
        require(fields is not None and "DESCRIPTION" in fields, "Source needs a DESCRIPTION header")
        require(len(fields) == len(set(fields)) and all(name.strip() for name in fields), "Invalid source column names")
        require(next(output, None) == fields + LABEL_FIELDS, "Column contract failed")
        audit_reader = None
        if audit_path is not None:
            audit_stream = stack.enter_context(open_csv(audit_path, "r"))
            audit_reader = csv.reader(audit_stream, strict=True)
            require(next(audit_reader, None) == AUDIT_FIELDS, "Audit column contract failed")
        for number, (before, after) in enumerate(itertools.zip_longest(source, output), 2):
            require(before is not None and after is not None, f"Row count differs at record {number}")
            require(len(before) == len(fields), f"Malformed source record {number}")
            require(len(after) == len(fields) + 3, f"Malformed output record {number}")
            require(after[:-3] == before, f"Original cells or order differ at record {number}")
            project, unit_type, action_label = after[-3:]
            require(project in {"Yes", "No"}, f"Invalid project label at record {number}")
            allowed_types = {"Attached", "Detached", "Unclear"} if project == "Yes" else {""}
            require(unit_type in allowed_types, f"Invalid type at record {number}")
            try:
                parts = action_components(action_label)
            except ValueError:
                raise VerificationError(f"Invalid action at record {number}") from None
            require(bool(parts) if project == "Yes" else not action_label, f"Invalid action at record {number}")
            record = dict(zip(fields, before))
            result = classify_description(record["DESCRIPTION"]) if reclassify else None
            action = classify_action(record["DESCRIPTION"], project) if reclassify else None
            if result is not None:
                require((project, unit_type) == (result.project, result.unit_type),
                        f"Saved labels differ from classifier at record {number}")
                require(action_label == action.label, f"Saved action differs from classifier at record {number}")
            if audit_reader is not None:
                audit = next(audit_reader, None)
                require(audit is not None and len(audit) == len(AUDIT_FIELDS), f"Missing or malformed audit record {number}")
                expected_prefix = [str(number), record.get("BUILDINGPERMITID", ""), record["DESCRIPTION"], project, unit_type]
                require(audit[:5] == expected_prefix, f"Audit identity or labels differ at record {number}")
                require(bool(audit[5].strip()), f"Missing audit reason at record {number}")
                audit_flags = audit[7].split("; ") if audit[7] else []
                require(len(audit_flags) == len(set(audit_flags)), f"Duplicate audit flags at record {number}")
                flags.update(audit_flags)
                require(audit[8] == action_label and bool(audit[9].strip()), f"Audit action differs at record {number}")
                audit_action_flags = audit[11].split("; ") if audit[11] else []
                require(len(audit_action_flags) == len(set(audit_action_flags)), f"Duplicate action flags at record {number}")
                action_flags.update(audit_action_flags)
                if result is not None:
                    expected_detail = [result.reason, "; ".join(result.evidence), "; ".join(result.flags)]
                    require(audit[5:8] == expected_detail, f"Audit evidence differs from classifier at record {number}")
                    expected_action = [action.label, action.reason, "; ".join(action.evidence), "; ".join(action.flags)]
                    require(audit[8:] == expected_action, f"Audit action evidence differs from classifier at record {number}")
            elif result is not None:
                flags.update(result.flags)
                action_flags.update(action.flags)
            counts[project] += 1
            if project == "Yes":
                types[unit_type] += 1
                actions[action_label] += 1
                components.update(parts)
            missing += not bool(record["DESCRIPTION"].strip())
            statuses[record.get("STATUS", "")] += 1
            dates[record.get("EFFECTIVEDATE", "")] += 1
            states[record.get("PROPERTYADDRESSSTATE", "")] += 1
            identifier = record.get("BUILDINGPERMITID", "")
            if identifier.strip():
                duplicate_ids += identifier in identifiers
                identifiers.add(identifier)
            rows += 1
        if audit_reader is not None:
            require(next(audit_reader, None) is None, "Extra audit records")
    nonempty_dates = [date for date in dates if date]
    expected = {
        "rows": rows, "original_columns": len(fields), "output_columns": len(fields) + 3,
        "project_labels": dict(counts), "adu_types": dict(types), "missing_descriptions": missing,
        "adu_actions": dict(actions), "adu_action_categories": dict(components), "action_categories": list(ACTION_CATEGORIES),
        "duplicate_permit_ids": duplicate_ids, "property_states": dict(states),
        "effective_dates": dict(dates), "statuses": dict(statuses),
        "effective_date_range": [min(nonempty_dates), max(nonempty_dates)] if nonempty_dates else [None, None],
        "input_sha256": sha256(source_path), "output_sha256": sha256(output_path),
    }
    if audit_path is not None or reclassify:
        expected["review_flags"] = dict(flags)
        expected["action_review_flags"] = dict(action_flags)
    for name, value in expected.items():
        require(name in summary and summary[name] == value, f"Summary mismatch: {name}")
    return {
        "rows_checked": rows, "original_columns_checked": len(fields), "original_cells_checked": rows * len(fields),
        "original_values_and_order_preserved": True, "label_contract_passed": True, "counts_reconciled": True,
        "source_and_output_hashes_passed": True, "summary_profiles_reconciled": True,
        "audit_records_checked": rows if audit_path is not None else 0,
        "predictions_reproduced": reclassify,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("summary")
    parser.add_argument("--audit")
    parser.add_argument("--reclassify", action="store_true", help="Reproduce every label and audit detail")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    receipt = Path(args.receipt).resolve()
    inputs = [Path(path).resolve() for path in (args.source, args.output, args.summary, args.audit) if path]
    try:
        require(receipt not in inputs, "Receipt must not overwrite an input")
        if receipt.exists():
            raise FileExistsError("Receipt already exists; choose a new destination")
        result = verify(args.source, args.output, args.summary, args.audit, args.reclassify)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
    except (ValueError, OSError, csv.Error, EOFError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
