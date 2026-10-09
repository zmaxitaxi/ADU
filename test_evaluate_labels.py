"""Independent arithmetic tests for sample evaluation, including exclusions."""
import unittest

from evaluate_labels import merge_reviews, score


def prediction(project, unit_type=""):
    return {"ADU project": project, "ADU type": unit_type}


def review(project, unit_type="", note="Manual judgment"):
    return {"review_project": project, "review_type": unit_type, "review_notes": note, "DESCRIPTION": "example"}


class EvaluationTests(unittest.TestCase):
    def test_confusion_matrix_and_uncertainty_exclusion(self):
        predicted = {"1": prediction("Yes", "Detached"), "2": prediction("No"),
                     "3": prediction("Yes", "Unclear"), "4": prediction("No"), "5": prediction("Yes", "Unclear")}
        actual = {"1": review("Yes", "Attached"), "2": review("Yes", "Detached"),
                  "3": review("No"), "4": review("No"), "5": review("Uncertain")}
        result = score(predicted, actual)
        self.assertEqual(result["confusion_matrix"], {"TP": 1, "FP": 1, "FN": 1, "TN": 1})
        for name in ("sample_precision", "sample_recall", "sample_f1", "sample_project_accuracy"):
            self.assertEqual(result[name], 0.5)
        self.assertEqual(result["uncertain_project_records_excluded"], 1)
        self.assertEqual(result["sample_type_label_agreement"], 0)
        self.assertEqual(len(result["errors"]), 3)

    def test_zero_denominators_are_unavailable(self):
        result = score({"1": prediction("No")}, {"1": review("No")})
        self.assertIsNone(result["sample_precision"])
        self.assertIsNone(result["sample_recall"])
        self.assertIsNone(result["sample_type_label_agreement"])

    def test_missing_or_extra_review_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            score({"1": prediction("No")}, {})

    def test_unlabeled_or_invalid_review_is_not_truth(self):
        for actual in (review(""), review("Yes", ""), review("No", "Unclear"), review("No", note="")):
            with self.assertRaises(ValueError):
                score({"1": prediction("No")}, {"1": actual})

    def test_project_disagreement_is_not_forced_negative(self):
        merged, counts = merge_reviews({"1": review("Yes", "Detached")}, {"1": review("No")})
        self.assertEqual(merged["1"]["review_project"], "Uncertain")
        self.assertEqual(counts, {"project_disagreement": 1})

    def test_merge_does_not_hide_invalid_individual_review(self):
        with self.assertRaises(ValueError):
            merge_reviews({"1": review("")}, {"1": review("No")})

    def test_type_disagreement_is_excluded_from_type_score(self):
        merged, _ = merge_reviews({"1": review("Yes", "Attached")}, {"1": review("Yes", "Detached")})
        result = score({"1": prediction("Yes", "Unclear")}, merged)
        self.assertEqual(result["confusion_matrix"]["TP"], 1)
        self.assertEqual(result["unresolved_type_records_excluded"], 1)
        self.assertIsNone(result["sample_type_label_agreement"])


if __name__ == "__main__":
    unittest.main()
