"""Purpose semantics: current ADU work, object scope, uncertainty and mixed actions.

Source cases are development regressions, not independent human ground truth.
"""
import unittest

from classify_adu import action_components, classify_action


class ActionTests(unittest.TestCase):
    def check_cases(self, cases):
        for text, expected in cases:
            with self.subTest(description=text):
                result = classify_action(text)
                self.assertEqual(result.label, expected)
                self.assertTrue(result.reason)
                if expected and expected != "Unclear":
                    self.assertTrue(result.evidence)

    def test_explicit_new_building_and_installation(self):
        self.check_cases([
            ("Construct new 2-bedroom detached ADU", "New construction"),
            ("Install a prefabricated ADU on permanent foundation", "New construction"),
            ("ADU new construction", "New construction"),
        ])

    def test_bare_new_unit_does_not_establish_pathway(self):
        self.check_cases([("New ADU", "Unclear"), ("New detached 800 sf ADU", "Unclear"), ("ADU", "Unclear")])
        self.assertIn("action_new_unit_pathway_unclear", classify_action("New ADU").flags)

    def test_conversion_precedes_new_adjective(self):
        self.check_cases([
            ("Convert garage to new detached ADU", "Conversion"),
            ("New ADU by converting existing garage", "Conversion"),
            ("Convert portion of primary house into JADU", "Conversion"),
        ])

    def test_addition_retains_actual_floor_area_work(self):
        self.check_cases([
            ("Addition - proposed addition (258.00 sqft) to an existing ADU", "Addition"),
            ("Add 250 sf to existing ADU", "Addition"),
            ("Convert (366 sf.) garage to ADU construct (223 sf.) to ADU", "Conversion; Addition"),
            ("Convert garage into ADU with a 200 sf addition", "Conversion; Addition"),
        ])

    def test_legalization_is_not_new_physical_conversion(self):
        self.check_cases([
            ("Legalize existing garage conversion to ADU", "Legalization"),
            ("Legalizing unpermitted detached ADU built before 2017", "Legalization"),
            ("Life-safety retrofits to legalize existing pre-2020 as-built ADU. No new square footage added", "Legalization; Repair/remodel"),
        ])

    def test_repair_and_internal_room_changes(self):
        self.check_cases([
            ("Replace water heater in existing detached ADU", "Repair/remodel"),
            ("Reroof existing ADU", "Repair/remodel"),
            ("Remodel ADU convert bonus room to 2nd bedroom", "Repair/remodel"),
        ])

    def test_demolition_and_use_reversal(self):
        self.check_cases([
            ("Demolish existing detached ADU", "Demolition/removal"),
            ("Demolish unpermitted JADU and convert back to existing garage", "Demolition/removal"),
            ("Convert existing ADU into a garage", "Demolition/removal"),
        ])

    def test_rebuild_differs_from_new_unit_and_keeps_removal(self):
        self.check_cases([
            ("Eaton fire rebuild - new detached ADU", "Reconstruction"),
            ("Demolish existing ADU and rebuild new ADU", "Demolition/removal; Reconstruction"),
        ])

    def test_standalone_trade_controls_over_underlying_building(self):
        self.check_cases([
            ("Mechanical for convert garage and addition to ADU", "Utility work"),
            ("Plumbing for construction of new ADU", "Utility work"),
            ("Mec permit for: new construction of detached ADU", "Utility work"),
            ("Ele permit for new ADU", "Utility work"),
            ("El for Eaton fire rebuild - ADU", "Utility work"),
            ("Se for Eaton fire rebuild - ADU", "Utility work"),
            ("Sewer connection for rebuild of ADU", "Utility work"),
        ])

    def test_equipment_installation_is_not_building_a_unit(self):
        self.check_cases([
            ("Install mini-split heat pump system for ADU", "Utility work"),
            ("Install fire sprinkler system for new ADU", "Utility work"),
            ("Install new subpanel for ADU", "Utility work"),
            ("Install solar panels for ADU", "Utility work"),
            ("Install roof mounted PV panels for ADU", "Utility work"),
            ("Remove sprincler from ADU", "Utility work"),
            ("Remove plumbing for illegal ADU", "Utility work"),
            ("Install new shingles to reroof ADU", "Repair/remodel"),
        ])

    def test_site_work_is_not_adu_building_or_removal(self):
        self.check_cases([
            ("Demolish existing detached garage for new ADU", "Site preparation"),
            ("Grading for new detached ADU", "Site preparation"),
            ("Traffic control to install prefab ADU", "Site preparation"),
            ("New detached ADU demolition of existing structure and paving", "Site preparation"),
            ("Demolish garage for new ADU. Construct the ADU", "New construction; Site preparation"),
        ])

    def test_administrative_scope_controls_underlying_actions(self):
        self.check_cases([
            ("Minor revision to remove sprinkler requirement for ADU", "Administrative"),
            ("Completion permit to construct a new ADU", "Administrative"),
            ("This completion permit refers to construction of an ADU", "Administrative"),
            ("Plan check for garage conversion to JADU", "Administrative"),
            ("Renew expired ADU conversion permit", "Administrative"),
            ("Request new address for attached ADU", "Administrative"),
            ("Establish address for new ADU", "Administrative"),
            ("**for certificate of occupancy** new ADU: garage conversion", "Administrative"),
        ])

    def test_other_objects_do_not_borrow_adu_actions(self):
        self.check_cases([
            ("Legalize main house. Convert garage to JADU", "Conversion"),
            ("Demolish barn & building new ADU", "New construction"),
            ("Construct garage addition and construct attached ADU", "New construction"),
            ("New SB9 residential unit constructed above existing ADU", "Unclear"),
            ("New ADU with garage demo: other-permit ADU address assigned", "Unclear"),
        ])

    def test_negated_demolition_does_not_remove_a_unit(self):
        self.check_cases([
            ("Existing ADU repair; no need to demolish existing ADU", "Repair/remodel"),
            ("Existing ADU repair. Demolition of the ADU is not proposed", "Repair/remodel"),
        ])

    def test_partial_removal_does_not_claim_whole_unit_loss(self):
        result = classify_action("257sf addition/remodel; remove 294sf of existing ADU & replace rear wood deck")
        self.assertEqual(result.label, "Repair/remodel")
        self.assertIn("action_partial_removal", result.flags)

    def test_statutory_quote_is_not_actual_conversion(self):
        self.check_cases([("Addition of factory-built (8) 2-bedroom ADU units on permanent foundations. "
            "This project is pursuant to: gov. Code allowing existing dwelling units to be converted to ADUs", "New construction")])

    def test_explicit_multiple_unit_actions_remain_separate(self):
        self.check_cases([
            ("Convert garage to ADU. Construct another detached ADU", "New construction; Conversion"),
            ("Demolish existing ADU. Construct another ADU at a different site", "New construction; Demolition/removal"),
        ])

    def test_existing_space_construction_wording_is_conversion(self):
        self.check_cases([
            ("Jr. ADU construction in existing garage. Will convert two garages into one ADU", "Conversion"),
            ("Construct (n) ADU in (e) conditioned habitable space", "Conversion"),
        ])

    def test_contextual_construction_does_not_override_current_purpose(self):
        self.check_cases([
            ("New construction photovoltaic solar on a detached ADU", "Utility work"),
            ("400 amp upgrade service for new construction and ADU", "Utility work"),
            ("Cross street: test. Description of work: minor grading for new ADU with temporary construction power", "Site preparation"),
            ("Backyard patio roof deck with a prefabricated pergola and a new detached ADU", "Unclear"),
            ("Owner opts to connect the new sewer line to existing one per reduction of new ADU construction area", "Utility work"),
        ])

    def test_alternative_outcomes_and_missing_action_stay_unclear(self):
        self.check_cases([("Existing ADU legalize/revert code case", "Unclear"), ("ADU", "Unclear")])

    def test_no_projects_never_have_action(self):
        self.check_cases([("", ""), ("HVAC replacement", ""), ("No new ADU proposed", ""),
            ("Home remodel. ADU under separate permit.", "")])

    def test_canonical_domain_rejects_unknown_duplicate_or_reordered_actions(self):
        self.assertEqual(action_components("New construction; Conversion"), ("New construction", "Conversion"))
        for label in ("Build", "conversion", "Conversion ", "Conversion; Conversion", "Unclear; Conversion", "Conversion; New construction"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                action_components(label)


if __name__ == "__main__":
    unittest.main()
