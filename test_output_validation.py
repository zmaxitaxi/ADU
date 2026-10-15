"""Corrupt exports deliberately to prove validation rejects bad outputs."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import verify_output
from classify_adu import process, sha256
from verify_output import VerificationError, verify


class OutputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.output, self.audit, self.summary = [self.root / name for name in
            ("input.csv", "output.csv", "audit.csv", "summary.json")]
        self.source.write_text("BUILDINGPERMITID,DESCRIPTION,STATUS,EFFECTIVEDATE\n"
            '001,"New detached ADU, café",issued,2026-09-01\n002,HVAC replacement,applied,2026-09-02\n', encoding="utf-8")
        process(self.source, self.output, self.audit, self.summary)

    def check(self, reclassify=True):
        return verify(self.source, self.output, self.summary, self.audit, reclassify)

    def read_rows(self, path):
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.reader(stream))

    def write_rows(self, path, rows):
        with path.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)

    def edit_summary(self, key, value):
        document = json.loads(self.summary.read_text())
        document[key] = value
        self.summary.write_text(json.dumps(document))

    def test_valid_export_and_audit_pass(self):
        result = self.check()
        self.assertTrue(result["predictions_reproduced"])
        self.assertEqual(result["audit_records_checked"], 2)

    def test_validator_is_read_only(self):
        files = [self.source, self.output, self.audit, self.summary]
        before = [sha256(path) for path in files]
        self.check()
        self.assertEqual([sha256(path) for path in files], before)

    def test_header_mutation_is_rejected(self):
        rows = self.read_rows(self.output)
        rows[0][-1] = "ADU TYPE"
        self.write_rows(self.output, rows)
        with self.assertRaisesRegex(VerificationError, "Column contract"):
            self.check()

    def test_original_cell_mutation_is_rejected(self):
        rows = self.read_rows(self.output)
        rows[1][0] = "1"
        self.write_rows(self.output, rows)
        with self.assertRaisesRegex(VerificationError, "Original cells"):
            self.check()

    def test_reordered_rows_are_rejected(self):
        rows = self.read_rows(self.output)
        rows[1:] = reversed(rows[1:])
        self.write_rows(self.output, rows)
        with self.assertRaisesRegex(VerificationError, "Original cells or order"):
            self.check()

    def test_missing_and_extra_rows_are_rejected(self):
        original = self.read_rows(self.output)
        for rows in (original[:-1], original + [original[-1]]):
            with self.subTest(records=len(rows)):
                self.write_rows(self.output, rows)
                with self.assertRaisesRegex(VerificationError, "Row count"):
                    self.check()

    def test_missing_and_extra_cells_are_rejected(self):
        original = self.read_rows(self.output)
        for record in (original[1][:-1], original[1] + ["extra"]):
            rows = [original[0], record, original[2]]
            self.write_rows(self.output, rows)
            with self.assertRaisesRegex(VerificationError, "Malformed output"):
                self.check()

    def test_label_whitespace_and_case_are_rejected(self):
        original = self.read_rows(self.output)
        for project, unit_type in (("yes", "Detached"), (" Yes", "Detached"),
                                   ("Yes", "detached"), ("Yes", "Detached "), ("Yes", "")):
            rows = [row[:] for row in original]
            rows[1][-3:-1] = [project, unit_type]
            self.write_rows(self.output, rows)
            with self.assertRaisesRegex(VerificationError, "Invalid"):
                self.check()

    def test_no_project_cannot_have_a_type(self):
        rows = self.read_rows(self.output)
        rows[2][-2] = "Unclear"
        self.write_rows(self.output, rows)
        with self.assertRaisesRegex(VerificationError, "Invalid type"):
            self.check()

    def test_invalid_missing_or_nonapplicable_actions_are_rejected(self):
        original = self.read_rows(self.output)
        for index, action in ((1, ""), (1, "Build"), (1, "Conversion; Unclear"),
                              (1, "Utility work; Conversion"), (2, "Repair/remodel")):
            with self.subTest(row=index, action=action):
                rows = [row[:] for row in original]
                rows[index][-1] = action
                self.write_rows(self.output, rows)
                with self.assertRaisesRegex(VerificationError, "Invalid action"):
                    self.check()

    def test_valid_but_incorrect_action_is_rejected_by_reclassification(self):
        rows = self.read_rows(self.output)
        rows[1][-1] = "Conversion"
        self.write_rows(self.output, rows)
        with self.assertRaisesRegex(VerificationError, "Saved action differs"):
            self.check()

    def test_action_evidence_and_flags_are_checked(self):
        initial = self.read_rows(self.audit)
        for column in (8, 9, 10, 11):
            with self.subTest(column=column):
                rows = [row[:] for row in initial]
                rows[1][column] = "incorrect"
                self.write_rows(self.audit, rows)
                with self.assertRaisesRegex(VerificationError, "Audit action"):
                    self.check()

    def test_action_summary_counts_are_checked(self):
        initial = self.summary.read_text()
        for key in ("adu_actions", "adu_action_categories", "action_categories", "action_review_flags"):
            with self.subTest(key=key):
                self.summary.write_text(initial)
                self.edit_summary(key, "wrong")
                with self.assertRaisesRegex(VerificationError, "Summary mismatch"):
                    self.check()

    def test_valid_but_incorrect_type_is_rejected_by_reclassification(self):
        rows = self.read_rows(self.output)
        rows[1][-2] = "Attached"
        self.write_rows(self.output, rows)
        with self.assertRaisesRegex(VerificationError, "Saved labels differ"):
            self.check()

    def test_summary_counts_and_profiles_are_rejected_if_changed(self):
        initial = self.summary.read_text()
        for name, value in (("rows", 3), ("project_labels", {}), ("adu_types", {}),
            ("missing_descriptions", 1), ("duplicate_permit_ids", 1), ("original_columns", 9),
            ("output_columns", 9), ("statuses", {}), ("effective_dates", {}),
            ("effective_date_range", [None, None]), ("property_states", {}), ("review_flags", {"invented": 1})):
            with self.subTest(field=name):
                self.summary.write_text(initial)
                self.edit_summary(name, value)
                with self.assertRaisesRegex(VerificationError, f"Summary mismatch: {name}"):
                    self.check()

    def test_missing_summary_field_is_rejected(self):
        document = json.loads(self.summary.read_text())
        del document["rows"]
        self.summary.write_text(json.dumps(document))
        with self.assertRaisesRegex(VerificationError, "Summary mismatch: rows"):
            self.check()

    def test_hash_mismatch_is_rejected(self):
        self.edit_summary("output_sha256", "0" * 64)
        with self.assertRaisesRegex(VerificationError, "Summary mismatch: output_sha256"):
            self.check()

    def test_audit_identity_and_labels_are_rejected_if_changed(self):
        initial = self.read_rows(self.audit)
        for column, value in ((0, "99"), (1, "wrong-id"), (2, "wrong-description"),
                              (3, "No"), (4, "Attached")):
            with self.subTest(column=column):
                rows = [row[:] for row in initial]
                rows[1][column] = value
                self.write_rows(self.audit, rows)
                with self.assertRaisesRegex(VerificationError, "Audit identity or labels"):
                    self.check()

    def test_missing_and_extra_audit_records_are_rejected(self):
        initial = self.read_rows(self.audit)
        for rows in (initial[:-1], initial + [initial[-1]]):
            self.write_rows(self.audit, rows)
            with self.assertRaisesRegex(VerificationError, "audit record"):
                self.check()

    def test_stale_reason_evidence_and_flags_are_rejected(self):
        initial = self.read_rows(self.audit)
        for column in (5, 6, 7):
            with self.subTest(column=column):
                rows = [row[:] for row in initial]
                rows[1][column] = "incorrect"
                self.write_rows(self.audit, rows)
                with self.assertRaisesRegex(VerificationError, "Audit evidence differs"):
                    self.check()

    def test_corruption_is_rejected_under_python_optimization(self):
        rows = self.read_rows(self.output)
        rows[1][0] = "wrong"
        self.write_rows(self.output, rows)
        receipt = self.root / "receipt.json"
        result = subprocess.run([sys.executable, "-O", str(Path(verify_output.__file__)),
            str(self.source), str(self.output), str(self.summary), "--receipt", str(receipt)],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Original cells", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
