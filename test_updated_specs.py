"""Checks arising from the October 9 task notes and explicit scope policy."""
import unittest

from classify_adu import classify_description


class UpdatedSpecificationTests(unittest.TestCase):
    def test_passing_reference_does_not_make_primary_residence_work_an_adu(self):
        for text in (
            "Convert existing 2 units to single family dwelling and small addition of 83 sf to enlarge master bedroom and walking closet. Adu under separate plan check and permit",
            "Interior remodel + 184 sf addition to single family residence + garage ref: comr-2025-0379 [detached adu] ref: demor-2025-0029 [demo garage] includes all related mechanical electrical and plumbing",
        ):
            with self.subTest(text=text):
                result = classify_description(text)
                self.assertEqual((result.project, result.unit_type), ("No", ""))
                self.assertIn("passing_adu_reference", result.flags)

    def test_direct_adu_work_survives_separate_permit_reference(self):
        for text in ("Grading drainage and stormwater control for new ADU. ADU under separate permit.",
                     "Construct new detached ADU. Plumbing under separate permit."):
            with self.subTest(text=text):
                self.assertEqual(classify_description(text).project, "Yes")

    def test_existing_adu_maintenance_still_counts(self):
        result = classify_description("Replace water heater in existing detached ADU")
        self.assertEqual((result.project, result.unit_type), ("Yes", "Detached"))

    def test_adjectives_do_not_break_explicit_negation(self):
        for text in ("No new attached ADU is proposed", "Not a detached ADU; pool house only",
                     "No proposed detached accessory dwelling unit", "Without an attached JADU"):
            with self.subTest(text=text):
                result = classify_description(text)
                self.assertEqual((result.project, result.unit_type), ("No", ""))

    def test_no_change_is_not_adu_negation(self):
        result = classify_description("Remodel attached ADU. No new bedrooms or bathrooms.")
        self.assertEqual((result.project, result.unit_type), ("Yes", "Attached"))

    def test_accessory_living_room_is_not_a_dwelling_unit(self):
        result = classify_description("Accessory living room addition")
        self.assertEqual((result.project, result.unit_type), ("No", ""))


if __name__ == "__main__":
    unittest.main()
