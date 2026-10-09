"""Independent source-grounded ADU detection regression cases.

These test DESCRIPTION semantics without consulting the other permit fields.
Permit IDs document provenance; test text is data, not operating instructions.
"""
import unittest

from classify_adu import classify_description


class DetectionReviewTests(unittest.TestCase):
    def test_source_grounded_detection(self):
        # project, type (None means this reviewer makes no assertion), description
        cases = [
            ("1228722530", "Yes", "Unclear", "New adu"),
            ("1228000563", "Yes", "Detached", "New detached 2-story two adus total 880 sf (each 440 sf studio with 1 bathroom)"),
            ("1228778357", "Yes", "Detached", "(b) new detached A.D.U. (1 000 sq ft)"),
            ("1227927825", "Yes", None, "Bar2600180 - partial garage conversion to j.a.d.u. (363 sqft)"),
            ("1229145074", "Yes", None, "Conversion of existing garage into new jradu"),
            ("1229341909", "Yes", "Detached", "New dadu on rear of property"),
            ("1227992007", "Yes", "Unclear", "We are splitting the 200a service into 120a for the aadu and 80a for the main house"),
            ("1226367815", "Yes", "Unclear", "Mechanical for new 814 sf aduu"),
            ("1229367263", "Yes", None, "Garage2jadu gas2elec dhw HVAC pool/spa panel&sewer upgrade m.bath alteration g.bath split2ensuites bath2/3 summer kitchen bathroom covered patio+open-frame pergola covered pool patio+open-frame pergola ev-chargers"),
            ("1228594389", "Yes", "Unclear", "570sfadu; patio drainage windows/doors siding 411sf pool"),
            ("1228524574", "Yes", "Attached", "* convert the existing attached garage into new accessary dwelling unit"),
            ("1228623193", "Yes", "Attached", "Conversion of 368 square feet of the existing single-family residence to a junior accessory dwelling unit (jadu) includes kitchen and private bathroom. No addition"),
            ("1228455571", "No", None, "Installation of an ess system 38.4kwh or less and/or flush roof mounted photovoltaic system on a residential single-family dwelling including adus 38kw or less. This installation shall comply with the electrical fire and building provisions of the most recent applicable code cycle"),
            ("1228438363", "No", None, "Two detached unconditioned non-habitable residential accessory structures. Not dwelling units or adus. No sleeping use. No space heating or cooling provided"),
            ("1227704474", "No", None, "Upgrade existing 125a underground electrical service to new 400a. Relocate main service panel approx 3 feet from the gas main to the left of the existing panel behind the fence line. No additional square footage or adu included in this scope of work"),
            ("1227675032", "No", None, "3046 single family home new factory built construction. No jadu will be included"),
            ("1227868990", "No", None, "Remove and replace 2 doors 57 sf on single family residence with no change to size or location. No adu on lot"),
            ("1213044556", "No", None, "Res- addition/residential remodel new 999 sf attached adu in basement remodel 354 sf and 410 sf addition to main house new attached 264sf conservatory and 500 sf detached two-car tandem garage (under permit 26bld-00992). Note: basement perimeter drain shown on sheet a3 not authorized per city policy ahmad. *scope changes 9/1/26 not doing adu just a basement space. Not doing detached garage either)"),
            ("1220443209", "No", None, "We are in the process of opening an adult residential facility 6 people max. Per fire dept occupancy needs to be changed to r-3.1"),
            ("1229403250", "No", None, "Detached guest house (406 sf) and covered patio (213 sf)"),
            ("1229005964", "No", None, "Addition to existing detached casita"),
            ("1228676189", "No", None, "Proposed 1 500 sq. Ft. Second unit with 160 sq. Ft. New porch 528 sq. Ft. New garage and 147 sq. Ft. New open patio"),
            ("1229431365", "Yes", "Unclear", "15 sqft mother in law quarters shower remodel like for like replacements only. Shower light is suitable got damp/wet location per cec 410.0 d. Bathroom circuit and switches will be updated per cec 150.0 k. New shower valve new shower drain new shower wall tile new shower pan tile. Led light/panasonic fan will remain as existing. Bathroom circuit and switches will be updated per cec 150.0 k"),
            ("synthetic-demolition", "Yes", "Unclear", "Demolish existing ADU"),
            ("empty", "No", None, ""),
        ]
        for permit_id, project, unit_type, description in cases:
            with self.subTest(permit_id=permit_id):
                result = classify_description(description)
                self.assertEqual(result.project, project)
                if unit_type is not None:
                    self.assertEqual(result.unit_type, unit_type)

    def test_formal_phrase_variants_found_in_prediction_audit(self):
        cases = [
            ("1190506243", "Electrical for a new detached accessory dwelling with 200 amp service panel", "Detached"),
            ("1226908079", "Sewer to construct a new detached 450 sq. Ft. Accessory dwelling. Studio/1bath refer to bldr250206001224", "Detached"),
            ("1227989729", "This project proposes the construcion of a new 429 s.f.accessory dweilling unit attached to the rear of the existing detached garage. Proposed unit is a studio unit with a kitchen and bathroom", "Detached"),
        ]
        for permit_id, description, unit_type in cases:
            with self.subTest(permit_id=permit_id):
                result = classify_description(description)
                self.assertEqual(result.project, "Yes")
                self.assertEqual(result.unit_type, unit_type)

    def test_cross_reference_does_not_establish_adu_scope(self):
        description = "Permit reassignment to new owner: permit cb25-0148: single story horizontal rear addition with one bedroom and one bathroom. Replace existing exterior stair with new interior stair to connect existing second floor (currently storage space to habitable space)to rear addition. Raise building approximate 1'-10. Replace existing foundation. Lateral strengthening of first floor new subpanel. - 1731 sq ft with 894 sq ft new conditioned (bldg/mech/elec/plumbing) *connected to pln24-0593 **shared plans with cb25-0148 & adu25-0030"
        self.assertEqual(classify_description(description).project, "No")
        # A unit identifier can still have explicit work-for-unit context.
        description = "Replacing existing electrical main panel 200 amps with 2 meters to be upgraded to 225 amps with 3 meters for suppling the new adu23-0013"
        self.assertEqual(classify_description(description).project, "Yes")

    def test_case_and_punctuation_do_not_change_detection(self):
        for text in ["NEW DETACHED A.D.U.", "new detached a.d.u.", "New detached ADU", "New detached ADUs"]:
            with self.subTest(description=text):
                result = classify_description(text)
                self.assertEqual(result.project, "Yes")
                self.assertEqual(result.unit_type, "Detached")


if __name__ == "__main__":
    unittest.main()
