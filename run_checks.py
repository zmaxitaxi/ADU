#!/usr/bin/env python3
"""Run regression tests, validate the delivered files, and test a fresh export.

Uses Python's standard library. Exits nonzero if any check fails. Data inputs
are never modified; a generated test report can be refreshed on later runs.
"""
import argparse
import gzip
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from classify_adu import VERSION, process
from verify_output import VerificationError, verify

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "deliverables/v1_1"
REPORT_KIND = "adu_test_report"


def csv_content_hash(path):
    digest = hashlib.sha256()
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save_report(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Re-running may replace only a prior report produced by this runner.
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict) or existing.get("report_kind") != REPORT_KIND:
            raise ValueError("Report destination already exists and is not an ADU test report")
    descriptor, temporary = tempfile.mkstemp(prefix=".adu-report-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Defaults to the source path recorded in the summary")
    parser.add_argument("--output", type=Path, default=DEFAULT_DATA / "california_september_2026_adu.csv.gz")
    parser.add_argument("--summary", type=Path, default=DEFAULT_DATA / "summary.json")
    parser.add_argument("--audit", type=Path, default=DEFAULT_DATA / "label_evidence.csv.gz")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/test_report.json")
    parser.add_argument("--unit-only", action="store_true", help="Skip checks requiring the full source dataset")
    args = parser.parse_args(argv)
    protected = [args.output.resolve(), args.summary.resolve(), args.audit.resolve()]
    protected.extend(path.resolve() for path in ROOT.glob("*.py"))
    protected.append((ROOT / "README.md").resolve())
    if args.source:
        protected.append(args.source.resolve())
    if args.report.resolve() in protected:
        parser.error("Report destination must not overwrite code, documentation, or data inputs")
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(ROOT), pattern="test_*.py", top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "schema_version": 1, "report_kind": REPORT_KIND,
        "completed_at_utc": None, "classifier_version": VERSION,
        "unit_tests": {"methods_run": result.testsRun, "passed": result.wasSuccessful(),
                       "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)},
        "full_output_validation": {"status": "skipped"}, "fresh_export": {"status": "skipped"},
        "limitations": ["Passing software and data-integrity tests does not establish population classification accuracy."],
    }
    if not args.unit_only and result.wasSuccessful():
        try:
            summary = json.loads(args.summary.read_text(encoding="utf-8"))
            source = args.source or Path(summary["input_path"])
            if args.report.resolve() == source.resolve():
                raise VerificationError("Report destination must not overwrite the source")
            validated = verify(source, args.output, args.summary, args.audit, reclassify=True)
            report["full_output_validation"] = {"status": "passed", **validated}
            with tempfile.TemporaryDirectory(prefix="adu-check-") as directory:
                temporary = Path(directory)
                output, audit, stats = [temporary / name for name in ("output.csv.gz", "audit.csv.gz", "summary.json")]
                process(source, output, audit, stats)
                fresh = verify(source, output, stats, audit, reclassify=True)
                if csv_content_hash(output) != csv_content_hash(args.output):
                    raise VerificationError("Fresh CSV content differs from delivered output")
                if csv_content_hash(audit) != csv_content_hash(args.audit):
                    raise VerificationError("Fresh audit content differs from delivered evidence")
                report["fresh_export"] = {"status": "passed", **fresh,
                    "delivered_csv_content_reproduced": True, "delivered_audit_content_reproduced": True}
        except Exception as error:
            key = "full_output_validation" if report["full_output_validation"]["status"] != "passed" else "fresh_export"
            report[key] = {"status": "failed", "error_type": type(error).__name__, "message": str(error)}
            print(f"{key}: FAILED: {error}", file=sys.stderr)
    checks = [report["full_output_validation"], report["fresh_export"]]
    passed = result.wasSuccessful() and (args.unit_only or all(check["status"] == "passed" for check in checks))
    report["status"] = "passed" if passed else "failed"
    report["mode"] = "unit-only" if args.unit_only else "full"
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    try:
        save_report(args.report, report)
    except (ValueError, OSError) as error:
        parser.exit(2, f"error writing report: {error}\n")
    print(f"\nOverall: {report['status'].upper()} ({report['mode']})")
    print(f"Tests: {result.testsRun} methods; {len(result.failures)} failures; {len(result.errors)} errors")
    for name in ("full_output_validation", "fresh_export"):
        print(f"{name}: {report[name]['status']}")
    print(f"Report: {args.report.resolve()}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
