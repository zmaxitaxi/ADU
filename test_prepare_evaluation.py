"""Sampling checks: blinding, reproducibility, and duplicate separation."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from prepare_evaluation import prepare


class SamplingTests(unittest.TestCase):
    def test_blinded_reproducible_sample_with_unique_properties_and_descriptions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.csv"
            rows = [
                ["s1", "p1", "New detached ADU", "", "", ""],
                ["s2", "p1", "New attached ADU", "", "", ""],
                ["s3", "p2", "Garage conversion", "ADU", "", ""],
                ["s4", "p3", "Garage conversion", "ADU", "", ""],
                ["s5", "p4", "New guest house", "", "", ""],
                ["s6", "p5", "Replace heater", "", "", ""],
            ]
            with source.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["BUILDINGPERMITID", "ATTOMID", "DESCRIPTION", "TYPE", "SUBTYPE", "PROJECTNAME"])
                writer.writerows(rows)
            first = prepare(source, root / "a", per_group=2, seed=7)
            second = prepare(source, root / "b", per_group=2, seed=7)
            self.assertEqual(first, second)
            self.assertEqual(first["sample_size"], 4)
            self.assertEqual(first["sampled_records_by_stratum"], dict.fromkeys(
                ["predicted_yes", "metadata_candidate_no", "ambiguous_description_no", "other_no"], 1))
            for name in ("blinded_review.csv", "predictions.csv"):
                self.assertEqual((root / "a" / name).read_bytes(), (root / "b" / name).read_bytes())
            with (root / "a/blinded_review.csv").open(newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, ["BUILDINGPERMITID", "DESCRIPTION", "review_project", "review_type", "review_notes"])
                sample = list(reader)
            self.assertTrue(all(not row[field] for row in sample for field in ("review_project", "review_type", "review_notes")))
            selected = {row["BUILDINGPERMITID"] for row in sample}
            properties = [row[1] for row in rows if row[0] in selected]
            self.assertEqual(len(properties), len(set(properties)))
            self.assertEqual(len(sample), len({row["DESCRIPTION"] for row in sample}))
            with self.assertRaises(FileExistsError):
                prepare(source, root / "a")

    def test_invalid_sample_size_rejected(self):
        with self.assertRaises(ValueError):
            prepare(Path("unused.csv"), Path("unused"), per_group=0)


if __name__ == "__main__":
    unittest.main()
