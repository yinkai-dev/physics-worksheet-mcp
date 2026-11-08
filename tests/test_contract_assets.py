import copy
import unittest

from physics_worksheet_mcp.render.contract import ContractError, validate_spec
from test_contract import valid_spec


class ExamSpec11ContractTests(unittest.TestCase):
    def spec11(self, representation="structured"):
        raw = valid_spec()
        raw["schema_version"] = "1.1"
        question = raw["questions"][0]
        question["representation"] = representation
        question["original_number"] = "13"
        question["assets"] = [{"asset_id": "a" * 64, "role": "question_figure" if representation == "structured" else "full_question_facsimile",
                               "required": True, "order": 1,
                               "placement": "after_stem" if representation == "structured" else "replace_question"}]
        if representation == "facsimile":
            question["body"] = []
            question["choices"] = []
        return raw

    def test_10_is_unchanged_and_11_accepts_only_logical_asset_references(self):
        legacy, _ = validate_spec(valid_spec())
        self.assertEqual(legacy["schema_version"], "1.0")
        normalized, _ = validate_spec(self.spec11())
        self.assertEqual(normalized["questions"][0]["assets"][0]["asset_id"], "a" * 64)
        self.assertEqual(normalized["questions"][0]["representation"], "structured")

    def test_11_accepts_reviewed_structured_question_without_images(self):
        raw = self.spec11()
        raw["questions"][0]["assets"] = []
        normalized, _ = validate_spec(raw)
        self.assertEqual(normalized["questions"][0]["assets"], [])

    def test_paths_urls_svg_duplicates_and_bad_facsimile_are_rejected(self):
        for field, value, error in (
            ("path", "assets/a.png", "unknown|path"),
            ("url", "https://example.invalid/a.png", "unknown|url"),
            ("media_type", "image/svg+xml", "unknown|media"),
        ):
            raw = self.spec11(); raw["questions"][0]["assets"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ContractError, error):
                validate_spec(raw)
        raw = self.spec11(); raw["questions"][0]["assets"] *= 2
        with self.assertRaisesRegex(ContractError, "duplicate|order"):
            validate_spec(raw)
        raw = self.spec11("facsimile"); raw["questions"][0]["body"] = [{"type": "text", "value": "must not duplicate OCR"}]
        with self.assertRaisesRegex(ContractError, "facsimile"):
            validate_spec(raw)

    def test_11_requires_approved_asset_relation_and_required_declared_assets(self):
        for mutate, error in (
            (lambda q: q.update(review_status="needs-review"), "needs-review"),
            (lambda q: q["assets"][0].update(required=False), "required"),
        ):
            raw = self.spec11(); mutate(raw["questions"][0])
            with self.assertRaisesRegex(ContractError, error):
                validate_spec(raw)

    def test_facsimile_choice_mode_may_be_declared_without_duplicate_choices(self):
        raw = self.spec11("facsimile")
        raw["questions"][0]["choice_mode"] = "single"
        normalized, _ = validate_spec(raw)
        self.assertEqual(normalized["questions"][0]["choice_mode"], "single")
        self.assertEqual(normalized["questions"][0]["choices"], [])

    def test_facsimile_accepts_multiple_ordered_replacement_crops(self):
        raw = self.spec11("facsimile")
        raw["questions"][0]["assets"].append({
            "asset_id": "b" * 64, "role": "full_question_facsimile",
            "required": True, "order": 2, "placement": "replace_question",
        })
        normalized, _ = validate_spec(raw)
        self.assertEqual([asset["order"] for asset in normalized["questions"][0]["assets"]], [1, 2])


if __name__ == "__main__":
    unittest.main()
