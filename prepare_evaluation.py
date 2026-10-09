#!/usr/bin/env python3
"""Prepare a blinded, stratified diagnostic sample for manual review.

Review labels are deliberately blank. Predictions and strata are separate.
Prior reviewed permits and their matched properties are excluded when available.
"""
import argparse
import csv
import json
import random
import re
from pathlib import Path

from classify_adu import VERSION, classify_description, normalize, open_csv, sha256

ROOT = Path(__file__).resolve().parent
METADATA_ADU = re.compile(r"\b(?:j?adus?|access(?:ory|ary) dwelling)\b", re.I)


def prepare(source, directory, per_group=20, seed=20261009):
    if per_group < 1:
        raise ValueError("per_group must be positive")
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError("Choose a new evaluation directory")
    old_files = [ROOT / "detection_validation_sample.json", ROOT / "type_validation_sample.json",
                 ROOT / "test_adu_detection.py", ROOT / "test_adu_types.py", ROOT / "test_updated_specs.py"]
    old_ids = set()
    for path in old_files:
        if path.exists():
            old_ids.update(re.findall(r"\b\d{9,10}\b", path.read_text()))
    with open_csv(source, "r") as stream:
        records = list(csv.DictReader(stream, strict=True))
    old_properties = {row["ATTOMID"] for row in records if row["BUILDINGPERMITID"] in old_ids and row["ATTOMID"]}
    old_descriptions = {normalize(row["DESCRIPTION"]) for row in records if row["BUILDINGPERMITID"] in old_ids}
    groups = {name: [] for name in ("predicted_yes", "metadata_candidate_no", "ambiguous_description_no", "other_no")}
    for row in records:
        if row["BUILDINGPERMITID"] in old_ids or row["ATTOMID"] in old_properties or normalize(row["DESCRIPTION"]) in old_descriptions:
            continue
        result = classify_description(row["DESCRIPTION"])
        if result.project == "Yes":
            group = "predicted_yes"
        elif any(METADATA_ADU.search(row.get(field, "")) for field in ("TYPE", "SUBTYPE", "PROJECTNAME")):
            group = "metadata_candidate_no"
        elif "ambiguous_dwelling_language" in result.flags:
            group = "ambiguous_description_no"
        else:
            group = "other_no"
        groups[group].append((row, result))
    rng = random.Random(seed)
    chosen, seen_properties, seen_descriptions = [], set(), set()
    for name, candidates in groups.items():
        candidates = candidates[:]
        rng.shuffle(candidates)
        count = 0
        for row, result in candidates:
            description = normalize(row["DESCRIPTION"])
            prop = row["ATTOMID"]
            if description in seen_descriptions or (prop and prop in seen_properties):
                continue
            chosen.append((name, row, result))
            seen_descriptions.add(description)
            if prop:
                seen_properties.add(prop)
            count += 1
            if count == per_group:
                break
    rng.shuffle(chosen)
    directory.mkdir(parents=True)
    with (directory / "blinded_review.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["BUILDINGPERMITID", "DESCRIPTION", "review_project", "review_type", "review_notes"])
        for _, row, _ in chosen:
            writer.writerow([row["BUILDINGPERMITID"], row["DESCRIPTION"], "", "", ""])
    with (directory / "predictions.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["BUILDINGPERMITID", "stratum", "ADU project", "ADU type"])
        for name, row, result in chosen:
            writer.writerow([row["BUILDINGPERMITID"], name, result.project, result.unit_type])
    manifest = {"seed": seed, "classifier_version": VERSION, "source_sha256": sha256(source),
        "sample_size": len(chosen), "requested_per_group": per_group,
        "sampled_records_by_stratum": {name: sum(group == name for group, _, _ in chosen) for name in groups},
        "eligible_records_by_stratum": {name: len(rows) for name, rows in groups.items()},
        "excluded_prior_permit_ids": len(old_ids), "excluded_prior_property_ids": len(old_properties),
        "sampling_notes": "Diagnostic, enriched sample with unique descriptions and nonblank property IDs. Not a probability sample of all permits; no population-weighted metrics.",
        "review_instructions": "Review DESCRIPTION only. Yes requires direct ADU work; include maintenance/legalization/demolition and explicit work for an ADU. Exclude generic eligibility and passing separate-permit references. In-law/granny/secondary-dwelling aliases count provisionally. Guesthouse/casita/second-unit alone do not establish an ADU. Use Uncertain for ambiguous project evidence; do not force a truth label. Attached/Detached refer to the primary dwelling; use Unclear without sufficient physical evidence. Leave type blank for No/Uncertain. Record reasoning."}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--per-group", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261009)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.directory, args.per_group, args.seed), indent=2))
