#!/usr/bin/env python3
"""Evaluate frozen predictions against explicit manual review labels.

Uncertain project judgments and unresolved type disagreements are excluded
explicitly. The metrics describe this reviewed sample, not the full population.
"""
import argparse
import collections
import csv
import json
from pathlib import Path


def load_records(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("Missing or duplicate CSV headers")
        records = {}
        for row in reader:
            identifier = row.get("BUILDINGPERMITID", "")
            if not identifier or identifier in records or None in row or any(value is None for value in row.values()):
                raise ValueError("Missing/duplicate permit ID or malformed row")
            records[identifier] = row
    return records


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def validate_review(identifier, review):
    project, unit_type = review.get("review_project"), review.get("review_type", "")
    if project not in {"Yes", "No", "Uncertain"}:
        raise ValueError(f"Missing/invalid review project for {identifier}")
    if unit_type not in ({"Attached", "Detached", "Unclear"} if project == "Yes" else {""}):
        raise ValueError(f"Invalid review type for {identifier}")
    if not review.get("review_notes", "").strip():
        raise ValueError(f"Missing manual-review rationale for {identifier}")


def score(predictions, reviews):
    if set(predictions) != set(reviews):
        raise ValueError("Predictions and reviews must cover exactly the same permit IDs")
    confusion = dict.fromkeys(("TP", "FP", "FN", "TN"), 0)
    type_matrix = collections.Counter()
    uncertain = unresolved_type = true_positive = true_negative = 0
    type_correct = type_count = 0
    errors = []
    for identifier in predictions:
        predicted, review = predictions[identifier], reviews[identifier]
        actual = review.get("review_project")
        actual_type = review.get("review_type", "")
        project, unit_type = predicted.get("ADU project"), predicted.get("ADU type")
        if project not in {"Yes", "No"} or unit_type not in ({"Attached", "Detached", "Unclear"} if project == "Yes" else {""}):
            raise ValueError(f"Invalid prediction labels for {identifier}")
        validate_review(identifier, review)
        if actual == "Uncertain":
            uncertain += 1
            continue
        key = ("TP" if project == "Yes" else "FN") if actual == "Yes" else ("FP" if project == "Yes" else "TN")
        confusion[key] += 1
        true_positive += actual == "Yes"
        true_negative += actual == "No"
        kind = None
        if project != actual:
            kind = "false_positive" if project == "Yes" else "false_negative"
        if actual == "Yes" and project == "Yes":
            if review.get("adjudication_status") == "type_disagreement":
                unresolved_type += 1
            else:
                type_matrix[(actual_type, unit_type)] += 1
                type_count += 1
                type_correct += actual_type == unit_type
                if actual_type != unit_type:
                    kind = "type_mismatch"
        if kind:
            errors.append({"BUILDINGPERMITID": identifier, "error": kind, "predicted_project": project,
                "review_project": actual, "predicted_type": unit_type, "review_type": actual_type,
                "review_notes": review["review_notes"]})
    tp, fp, fn, tn = (confusion[name] for name in ("TP", "FP", "FN", "TN"))
    return {
        "sample_records": len(predictions), "scored_project_records": tp + fp + fn + tn,
        "uncertain_project_records_excluded": uncertain, "review_yes": true_positive, "review_no": true_negative,
        "confusion_matrix": confusion, "sample_precision": ratio(tp, tp + fp),
        "sample_recall": ratio(tp, tp + fn), "sample_f1": ratio(2 * tp, 2 * tp + fp + fn),
        "sample_project_accuracy": ratio(tp + tn, tp + fp + fn + tn),
        "type_records_scored_among_project_true_positives": type_count,
        "unresolved_type_records_excluded": unresolved_type,
        "sample_type_label_agreement": ratio(type_correct, type_count),
        "type_confusion": [{"review_type": actual, "predicted_type": predicted, "records": count}
                           for (actual, predicted), count in sorted(type_matrix.items())],
        "errors": errors,
        "limitations": ["Diagnostic sample metrics are unweighted and are not population accuracy estimates.",
            "Uncertain judgments and unresolved type disagreements are explicitly excluded from their respective metrics.",
            "Agreement with AI manual reviewers is not independently established human ground truth."],
    }


def merge_reviews(a, b):
    if set(a) != set(b):
        raise ValueError("Reviewers must cover the same IDs")
    merged = {}
    counts = collections.Counter()
    for identifier, left in a.items():
        right = b[identifier]
        validate_review(identifier, left)
        validate_review(identifier, right)
        if left.get("DESCRIPTION") != right.get("DESCRIPTION"):
            raise ValueError("Reviewer descriptions differ")
        project, unit_type = left["review_project"], left["review_type"]
        status = "agreed"
        if project != right["review_project"]:
            project, unit_type, status = "Uncertain", "", "project_disagreement"
        elif project == "Uncertain":
            status = "uncertain"
        elif project == "Yes" and unit_type != right["review_type"]:
            unit_type, status = "Unclear", "type_disagreement"
        counts[status] += 1
        merged[identifier] = {**left, "review_project": project, "review_type": unit_type,
            "review_notes": f"Reviewer A: {left['review_notes']} Reviewer B: {right['review_notes']}",
            "adjudication_status": status}
    return merged, dict(counts)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("review_a", type=Path)
    parser.add_argument("--review-b", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Choose a new report path")
    predictions, reviews = load_records(args.predictions), load_records(args.review_a)
    agreements = None
    if args.review_b:
        reviews, agreements = merge_reviews(reviews, load_records(args.review_b))
    result = score(predictions, reviews)
    result["reviewer_agreement"] = agreements
    result["review_provenance"] = "Manual text review by AI agents; no human adjudication"
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({name: value for name, value in result.items() if name not in ("errors", "limitations", "type_confusion")}, indent=2))
