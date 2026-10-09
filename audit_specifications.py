#!/usr/bin/env python3
"""Write a read-only source/label audit and metadata review queue.

Metadata flags are candidates, never automatic ADU labels. All outputs go to a
new directory; the source and both compared exports remain untouched.
"""
import argparse
import collections
import csv
import json
from pathlib import Path

from classify_adu import open_csv, sha256
from prepare_evaluation import METADATA_ADU


def audit(source, current, previous, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    labels = {}
    for name, path in (("current", current), ("previous", previous)):
        with open_csv(path, "r") as stream:
            labels[name] = {row["BUILDINGPERMITID"]: (row["ADU project"], row["ADU type"]) for row in csv.DictReader(stream)}
    counts, field_counts = collections.Counter(), collections.Counter()
    properties = set()
    source_ids = set()
    with open_csv(source, "r") as stream, (directory / "metadata_candidates.csv").open("x", encoding="utf-8", newline="") as candidates, (directory / "label_changes.csv").open("x", encoding="utf-8", newline="") as changes:
        candidate_writer, change_writer = csv.writer(candidates), csv.writer(changes)
        candidate_writer.writerow(["BUILDINGPERMITID", "ATTOMID", "DESCRIPTION", "TYPE", "SUBTYPE", "PROJECTNAME", "matched_fields"])
        change_writer.writerow(["BUILDINGPERMITID", "DESCRIPTION", "old_project", "old_type", "new_project", "new_type"])
        for row in csv.DictReader(stream, strict=True):
            identifier = row["BUILDINGPERMITID"]
            if identifier in source_ids:
                raise ValueError("Duplicate permit IDs require row-based audit")
            source_ids.add(identifier)
            current_label, old_label = labels["current"][identifier], labels["previous"][identifier]
            counts["rows"] += 1
            if not row["ATTOMID"].strip():
                counts["blank_ATTOMID"] += 1
            if current_label[0] == "Yes":
                counts["adu_permits"] += 1
                if row["ATTOMID"].strip():
                    properties.add(row["ATTOMID"])
                else:
                    counts["adu_permits_blank_ATTOMID"] += 1
            if current_label != old_label:
                counts["changed_labels"] += 1
                change_writer.writerow([identifier, row["DESCRIPTION"], *old_label, *current_label])
            fields = [field for field in ("TYPE", "SUBTYPE", "PROJECTNAME") if METADATA_ADU.search(row[field])]
            if current_label[0] == "No" and fields:
                counts["metadata_candidate_no"] += 1
                counts["metadata_candidate_no_blank_description"] += not row["DESCRIPTION"].strip()
                field_counts.update(fields)
                candidate_writer.writerow([identifier, row["ATTOMID"], row["DESCRIPTION"], row["TYPE"], row["SUBTYPE"], row["PROJECTNAME"], ";".join(fields)])
    if any(set(table) != source_ids for table in labels.values()):
        raise ValueError("Exports and source have different permit ID sets")
    result = {"counts": dict(counts), "unique_nonblank_ADU_ATTOMID": len(properties),
        "metadata_candidate_no_by_field_overlapping": dict(field_counts),
        "inputs": {"source": {"path": str(Path(source).resolve()), "sha256": sha256(source)},
                   "current": {"path": str(Path(current).resolve()), "sha256": sha256(current)},
                   "previous": {"path": str(Path(previous).resolve()), "sha256": sha256(previous)}},
        "limitations": ["ATTOMID is a provider property linkage key, not a validated parcel/imagery join.",
            "Metadata candidate fields overlap and contain generic categories; candidates are not confirmed ADUs.",
            "ADU permits include maintenance and administrative work; labels do not establish new construction, completion, or visibility in imagery."]}
    (directory / "profile.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "current", "previous", "directory"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.source, args.current, args.previous, args.directory), indent=2))
