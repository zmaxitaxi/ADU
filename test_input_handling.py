"""Boundary tests for invalid exports, empty exports, and transactional output."""
import csv
import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import classify_adu
from classify_adu import process, sha256


class InputHandlingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "input.csv"
        self.paths = [self.root / name for name in ("output.csv.gz", "audit.csv.gz", "summary.json")]

    def run_export(self):
        return process(self.source, *self.paths)

    def assert_no_outputs(self):
        self.assertFalse(any(path.exists() for path in self.paths))
        self.assertFalse(any(path.name.startswith(".adu-") for path in self.root.iterdir()))

    def test_header_only_export_is_valid(self):
        self.source.write_text("DESCRIPTION\n", encoding="utf-8")
        result = self.run_export()
        self.assertEqual(result["rows"], 0)
        self.assertEqual(result["effective_date_range"], [None, None])
        with gzip.open(self.paths[0], "rt", newline="") as stream:
            self.assertEqual(list(csv.reader(stream)), [["DESCRIPTION", "ADU project", "ADU type"]])

    def test_minimum_schema_and_utf8_bom(self):
        self.source.write_text('DESCRIPTION\n"New detached ADU, café"\n', encoding="utf-8-sig")
        result = self.run_export()
        self.assertEqual(result["project_labels"], {"Yes": 1})
        self.assertEqual(result["duplicate_permit_ids"], 0)

    def test_invalid_headers_leave_no_outputs(self):
        for text in ("", "OTHER\nHVAC\n", "DESCRIPTION,DESCRIPTION\na,b\n",
                     "DESCRIPTION,\nADU,x\n", "DESCRIPTION,ADU project\nADU,Yes\n",
                     "DESCRIPTION,ADU type\nADU,Detached\n"):
            with self.subTest(header=text.splitlines()[:1]):
                self.source.write_text(text)
                with self.assertRaises(ValueError):
                    self.run_export()
                self.assert_no_outputs()

    def test_malformed_records_leave_no_partial_output(self):
        for text in ("DESCRIPTION,ID\nADU,1\nmissing field\n",
                     "DESCRIPTION,ID\nADU,1\nextra,2,value\n",
                     'DESCRIPTION\n"unterminated\n'):
            with self.subTest(csv=text):
                self.source.write_text(text)
                with self.assertRaises((ValueError, csv.Error)):
                    self.run_export()
                self.assert_no_outputs()

    def test_invalid_encoding_leaves_no_outputs(self):
        self.source.write_bytes(b"DESCRIPTION\n\xff\n")
        with self.assertRaises(UnicodeError):
            self.run_export()
        self.assert_no_outputs()

    def test_missing_input_leaves_no_outputs(self):
        with self.assertRaises(FileNotFoundError):
            self.run_export()
        self.assert_no_outputs()

    def test_corrupt_gzip_leaves_no_outputs(self):
        self.source = self.root / "bad.csv.gz"
        self.source.write_bytes(b"not a gzip stream")
        with self.assertRaises(OSError):
            self.run_export()
        self.assert_no_outputs()

    def test_classifier_failure_leaves_no_outputs_and_preserves_source(self):
        self.source.write_text("DESCRIPTION\nNew ADU\n")
        before = sha256(self.source)
        with mock.patch.object(classify_adu, "classify_description", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                self.run_export()
        self.assert_no_outputs()
        self.assertEqual(sha256(self.source), before)

    def test_every_destination_is_protected_from_overwrite(self):
        self.source.write_text("DESCRIPTION\nNew ADU\n")
        for existing in self.paths:
            with self.subTest(destination=existing.name):
                existing.write_text("keep me")
                with self.assertRaises(FileExistsError):
                    self.run_export()
                self.assertEqual(existing.read_text(), "keep me")
                self.assertFalse(any(path.exists() for path in self.paths if path != existing))
                existing.unlink()

    def test_destination_aliases_are_rejected(self):
        self.source.write_text("DESCRIPTION\nADU\n")
        with self.assertRaises(ValueError):
            process(self.source, self.paths[0], self.paths[0], self.paths[2])
        self.assert_no_outputs()

    def test_description_only_and_repeated_blank_identifiers(self):
        self.source.write_text("DESCRIPTION,BUILDINGPERMITID\nADU,\nHVAC,\n")
        result = self.run_export()
        self.assertEqual(result["duplicate_permit_ids"], 0)

    def test_cli_error_is_readable_and_has_no_traceback(self):
        self.source.write_text("OTHER\nHVAC\n")
        result = subprocess.run([sys.executable, str(Path(classify_adu.__file__)), str(self.source),
            str(self.paths[0]), "--audit", str(self.paths[1]), "--summary", str(self.paths[2])],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DESCRIPTION", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assert_no_outputs()

    def test_publication_failure_rolls_back_own_outputs(self):
        self.source.write_text("DESCRIPTION\nNew detached ADU\n")
        actual_link = classify_adu.os.link
        calls = 0

        def fail_second_link(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated publication error")
            return actual_link(source, destination)

        with mock.patch.object(classify_adu.os, "link", side_effect=fail_second_link):
            with self.assertRaises(OSError):
                self.run_export()
        self.assert_no_outputs()

    def test_file_created_during_processing_is_not_overwritten(self):
        self.source.write_text("DESCRIPTION\nNew detached ADU\n")
        actual_link = classify_adu.os.link

        def competing_write(source, destination):
            if Path(destination) == self.paths[1]:
                self.paths[1].write_text("another process owns this")
            return actual_link(source, destination)

        with mock.patch.object(classify_adu.os, "link", side_effect=competing_write):
            with self.assertRaises(FileExistsError):
                self.run_export()
        self.assertEqual(self.paths[1].read_text(), "another process owns this")
        self.assertFalse(self.paths[0].exists())
        self.assertFalse(self.paths[2].exists())


if __name__ == "__main__":
    unittest.main()
