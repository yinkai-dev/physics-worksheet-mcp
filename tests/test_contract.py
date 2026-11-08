import copy
import hashlib
import unittest

from physics_worksheet_mcp.render.contract import ContractError, validate_spec


def valid_spec():
    excerpt = "合成测试题：物体做匀速直线运动。求速度。"
    return {
        "schema_version": "1.0", "document": {"title": "合成物理练习"},
        "questions": [{"id": "q1", "body": [{"type": "text", "value": excerpt}],
                       "source": {"kb_name": "合成测试", "title": "fixture.md", "page": None,
                                  "chunk_id": "synthetic-1", "source_excerpt": excerpt,
                                  "content_sha256": hashlib.sha256(excerpt.encode()).hexdigest()},
                       "review_status": "retrieved-complete"}],
    }


class ContractTests(unittest.TestCase):
    def test_rich_library_topic_can_exceed_the_old_135_question_limit(self):
        raw=valid_spec();raw['schema_version']='1.2'
        question=raw['questions'][0]
        raw['questions']=[dict(copy.deepcopy(question),id='q'+str(i),original_number=str(i+1)) for i in range(160)]
        normalized,_=validate_spec(raw)
        self.assertEqual(len(normalized['questions']),160)

    def test_normalizes_default_answers_without_mutating_input(self):
        raw = valid_spec()
        before = copy.deepcopy(raw)
        normalized, warnings = validate_spec(raw)
        self.assertIs(normalized["document"]["include_answers"], False)
        self.assertIsNone(normalized["questions"][0]["source"]["page"])
        self.assertTrue(any("page-unknown" in warning for warning in warnings))
        self.assertEqual(raw, before)

    def test_rejects_missing_source_and_needs_review(self):
        for mutation in (lambda q: q.pop("source"), lambda q: q.update(review_status="needs-review")):
            raw = valid_spec()
            mutation(raw["questions"][0])
            with self.assertRaises(ContractError):
                validate_spec(raw)

    def test_checks_original_excerpt_hash(self):
        raw = valid_spec()
        raw["questions"][0]["source"]["source_excerpt"] += " "
        with self.assertRaisesRegex(ContractError, "hash-mismatch"):
            validate_spec(raw)

    def test_rejects_dangerous_math_images_long_input_and_duplicate_ids(self):
        for block in ({"type": "math", "value": r"\input{/etc/passwd}"},
                      {"type": "image", "path": "../../secret.png"},
                      {"type": "text", "value": "长" * 8001}):
            raw = valid_spec()
            raw["questions"][0]["body"] = [block]
            with self.assertRaises(ContractError):
                validate_spec(raw)
        raw = valid_spec()
        raw["questions"].append(copy.deepcopy(raw["questions"][0]))
        with self.assertRaisesRegex(ContractError, "duplicate"):
            validate_spec(raw)

    def test_answer_requires_explicit_opt_in_and_origin(self):
        raw = valid_spec()
        raw["questions"][0]["answer"] = {"origin": "model", "body": [{"type": "text", "value": "这是模型解答。"}]}
        normalized, warnings = validate_spec(raw)
        self.assertNotIn("answer", normalized["questions"][0])
        self.assertTrue(any("answer-omitted" in w for w in warnings))
        raw["document"]["include_answers"] = True
        normalized, warnings = validate_spec(raw)
        self.assertEqual(normalized["questions"][0]["answer"]["origin"], "model")
        self.assertTrue(any("model-answer" in w for w in warnings))

    def test_choices_support_explicit_math_blocks_and_reject_injection(self):
        raw = valid_spec()
        raw["questions"][0]["choices"] = ["A. Plain $text$", [{"type": "text", "value": "B. "},
                                                                     {"type": "math", "value": r"\mathrm{N/C}"}]]
        normalized, _ = validate_spec(raw)
        self.assertEqual(normalized["questions"][0]["choices"][1][1]["type"], "math")
        raw["questions"][0]["choices"][1][1]["value"] = r"\input{/etc/passwd}"
        with self.assertRaisesRegex(ContractError, "unsafe-math"):
            validate_spec(raw)


if __name__ == "__main__":
    unittest.main()
