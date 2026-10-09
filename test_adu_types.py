"""Independent description-only regression cases for physical ADU type.

Source examples use BUILDINGPERMITID; synthetic cases explicitly say synthetic.
Attached/Detached describe the ADU's relation to the primary dwelling.
"""
import unittest

from classify_adu import classify_description


class ADUTypeReviewTests(unittest.TestCase):
    def test_source_and_boundary_cases(self):
        cases = [
            (
                "1226275935", "Detached",
                "Eaton fire rebuild - new detached 2-story 1 198 sf adu (3 bed 2 bathroom 1 powder) with 454 sf attached garage and 47 sf covered front porch",
            ),
            (
                "1228664342", "Detached",
                "El for new 270 sf adu (studio) attached to detached garage",
            ),
            (
                "1228202066", "Detached",
                "Sewr for (n) 1 200 sf detached adu attached to (e) garage",
            ),
            (
                "1221432425", "Detached",
                "Aca 2 detached adus attached to each other solar c/s colorado",
            ),
            (
                "1228695904", "Detached",
                "Convert existing detached single car garage to a adu with 3/4 bathroom kitchen and great room with closet",
            ),
            (
                "1227715808", "Attached",
                "B26-002017 - adu garage conversion: convert 1200 sf (attached) garage into adu",
            ),
            (
                "1228711675", "Attached",
                "Convert (e) 392 sq.ft. Attached garage to adu. 40 sq.ft. Remodel to (e) single family dwelling portion of playroom to bathroom",
            ),
            (
                "1227672925", "Attached",
                "Remodel (e) two-story single-family residence provide a 405 sf addition convert (e) 418 sf attached garage into an attached adu & construct a new 525 sf detached two-car garage",
            ),
            (
                "1228779032", "Unclear",
                "Adu - accessory dwelling unit 570 sf 1 bed 1 bathroom (garage conversion 441 sf and 129 sf addition)",
            ),
            (
                "1228780673", "Unclear",
                "Adu 1 171 sf attached garage 677 sf",
            ),
            (
                "1228341424", "Unclear",
                "(1)adu one story with attached garage",
            ),
            (
                "1228440868", "Unclear",
                "(n) 410 sf garage addition and 798 sf adu over (e) garage",
            ),
            (
                "1201973289", "Attached",
                "(n) 525 sf 2-story adu above (e) attached garage w expansion of 61 sf at (e) garage for (n) staircase ***1 bed 2 bath***",
            ),
            (
                "1228583741", "Unclear", "Jadu (493 sf)",
            ),
            (
                "1228623193", "Attached",
                "Conversion of 368 square feet of the existing single-family residence to a junior accessory dwelling unit (jadu) includes kitchen and private bathroom. No addition",
            ),
            (
                "1227818701", "Unclear",
                "Mid-city:eastern area: combination building permit for the remodel to an existing sdu to create a new attached jadu and a new detached 2-story duplex adu. Scope of work includes removal of unpermitted structures and new pcc pathway to adus. Rs-1-7; vhfsz",
            ),
            (
                "1177817183", "Unclear",
                "Provide fire sprinkler in detached adu and attached adus",
            ),
            (
                "1229374309", "Unclear",
                "Fire rebuild - none like for like new single family house with attached adu and attached garage and detached 2-story adu",
            ),
            (
                "1228756711", "Detached", "799 sq ft detach adu to rear of lot",
            ),
            (
                "1228709524", "Attached", "511 sqft attach adu",
            ),
            (
                "synthetic-nearby-garage", "Unclear",
                "Construct a new ADU. Remodel the existing attached garage.",
            ),
            (
                "synthetic-nearby-house", "Unclear",
                "Construct attached garage for main house and build a new ADU.",
            ),
            (
                "synthetic-separate-meter", "Unclear",
                "New ADU with separate meter and shared utilities.",
            ),
            (
                "synthetic-explicit-main-house", "Attached",
                "New ADU attached to the main house.",
            ),
        ]
        for identifier, expected_type, description in cases:
            with self.subTest(permit=identifier):
                result = classify_description(description)
                self.assertEqual(result.project, "Yes")
                self.assertEqual(result.unit_type, expected_type)


class ADUTypeAuditRegressionTests(unittest.TestCase):
    def test_audited_source_failures(self):
        cases = [
            ('1229190036', 'Unclear', 'For non permitted structures jadu detached arts and crafts structure storage sheds'),
            ('1189396381', 'Detached', '1198 sf [n] 2-story attached accessory dwelling unit (adu) to [e] detached garage to include 4 bedrooms and 2 bathrooms'),
            ('1189401630', 'Detached', '1198 sf [n] 2-story attached accessory dwelling unit (adu) to [e] detached garage to include 4 bedrooms and 2 bathrooms. (1) combo unit and (3) minisplit'),
            ('1189442366', 'Detached', '1198 sf [n] 2-story attached accessory dwelling unit (adu) to [e] detached garage to include 4 bedrooms and 2 bathrooms'),
            ('1189780824', 'Detached', '1198 sf [n] 2-story attached accessory dwelling unit (adu) to [e] detached garage to include 4 bedrooms and 2 bathrooms'),
            ('1189375251', 'Unclear', 'Eaton fire rebuild. New detached 392 sf garage with attached 126 sf office and 80 sf laundry and 601 sf 2nd story adu (1 bed 1 bath)'),
            ('1227682775', 'Unclear', 'Application for delay of enforcement. Property was purchased 07/31/26 with original garage converted into a jadu in 2019. It is less than 500sqft and has its own bathroom and kitchen area. Utilities are connected to the main house. On 8/21/26 a code enforcement officer let us know there was a complaint made earlier in the month and gave us an nov but informed us of the possibility of an enforcement delay'),
            ('1229034542', 'Unclear', 'Convert existing guest unit (bld00-0749) into an accessory dwelling unit by adding a cooktop sink and refrigerator (see attached plans and photos). This is to resolve a code enforcement violation (vbu26-0053) for an unpermitted adu conversion. Well yield test approved under wel26-0145. Thank you'),
            ('1229068623', 'Unclear', 'Convert existing guest unit (bld00-0749) into an accessory dwelling unit by adding a cooktop sink and refrigerator (see attached plans and photos). This is to resolve a code enforcement violation (vbu26-0053) for an unpermitted adu conversion. Well yield test approved under wel26-0145. Thank you'),
            ('1229638140', 'Detached', '(n) detached adu unit b 750 sf 2 bed 2 bathroom attached to adu unit a'),
        ]
        for identifier, expected_type, description in cases:
            with self.subTest(permit=identifier):
                result = classify_description(description)
                self.assertEqual(result.project, "Yes")
                self.assertEqual(result.unit_type, expected_type)


if __name__ == "__main__":
    unittest.main()
