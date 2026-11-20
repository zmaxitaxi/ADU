"""Round-trip checks for CSV preservation and output contracts."""
import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from classify_adu import classify_description, process, sha256
from verify_output import verify


class PipelineTests(unittest.TestCase):
    def test_original_cells_and_row_order_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, audit, summary = [root / name for name in
                ("input.csv.gz", "output.csv.gz", "audit.csv.gz", "summary.json")]
            fields = ["BUILDINGPERMITID", "DESCRIPTION", "ZIP", "STATUS", "EFFECTIVEDATE",
                      "PROJECTNAME", "TYPE", "SUBTYPE"]
            rows = [
                ["001", 'New detached ADU, café\n"rear unit"', "01234", "applied", "2026-09-01",
                 "New attached ADU", "", ""],
                ["002", "HVAC replacement", "00123", "issued", "2026-09-02",
                 "", "Mechanical", "Residential"],
                ["003", " \t", "00001", "canceled", "2026-09-03", " ", "ADU permit", ""],
                ["004", "Install a new sewer connection.", "", "", "",
                 "Sewer connection for a detached accessory dwelling unit (ADU)", "Sewer permit", "Residential"],
                ["005", "Bathroom remodel", "", "", "", "", "Residential ADU alterations", ""],
                ["006", "Re-roof", "", "", "", "", "Residential", "ADU work"],
                ["007", "", "", "", "", "Kitchen remodel", "", ""],
            ]
            with gzip.open(source, "wt", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(fields)
                writer.writerows(rows)
            before = sha256(source)
            result = process(source, output, audit, summary)
            with gzip.open(output, "rt", encoding="utf-8", newline="") as stream:
                saved = list(csv.reader(stream))
            self.assertEqual(saved[0], fields + ["ADU project", "ADU type", "ADU action"])
            self.assertEqual([row[:-3] for row in saved[1:]], rows)
            self.assertEqual([row[-3:] for row in saved[1:]],
                             [["Yes", "Detached", "Unclear"], ["No", "", ""], ["Review", "", ""],
                              ["Yes", "Detached", "Unclear"], ["Review", "", ""], ["Review", "", ""], ["No", "", ""]])
            with gzip.open(audit, "rt", encoding="utf-8", newline="") as stream:
                evidence = {row["BUILDINGPERMITID"]: row for row in csv.DictReader(stream)}
            self.assertIn("no_permit_text", evidence["003"]["review_flags"])
            self.assertNotIn("category_only_match", evidence["003"]["review_flags"])
            self.assertTrue(evidence["004"]["reason"].startswith("Project title:"))
            for permit, field in (("005", "TYPE"), ("006", "SUBTYPE")):
                self.assertIn("category_only_match", evidence[permit]["review_flags"])
                self.assertEqual(evidence[permit]["reason"], f"ADU evidence only in {field}")
            self.assertEqual(sha256(source), before)
            self.assertEqual(result["project_labels"], {"Yes": 2, "No": 2, "Review": 3})
            self.assertEqual(json.loads(summary.read_text())["missing_descriptions"], 2)
            self.assertEqual(verify(source, output, summary, audit, reclassify=True)["rows_checked"], len(rows))


    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.csv"
            output.write_text("existing result")
            with self.assertRaises(FileExistsError):
                process(root / "input.csv", output, root / "audit.csv", root / "summary.json")
            self.assertEqual(output.read_text(), "existing result")

    def test_input_cannot_be_used_as_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                process(root / "input.csv", root / "input.csv", root / "audit.csv", root / "summary.json")

    def test_multiple_units_with_incomplete_type(self):
        result = classify_description("Convert existing garage into 1 200 sf adu and addition attached 500 sf j.adu")
        self.assertEqual((result.project, result.unit_type), ("Yes", "Unclear"))

    def test_negated_attachment_is_not_positive_attachment(self):
        for text in ("New ADU not attached to the main house", "New not detached ADU"):
            self.assertEqual(classify_description(text).unit_type, "Unclear")


if __name__ == "__main__":
    unittest.main()
