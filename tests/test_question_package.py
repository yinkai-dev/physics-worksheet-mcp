import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import fitz


ADAPTER = Path(__file__).resolve().parents[1] / "src/physics_worksheet_mcp/bank/package.py"


def load_module():
    spec = importlib.util.spec_from_file_location("test_question_package_runtime", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha(data):
    return hashlib.sha256(data).hexdigest()


def jsonl(rows):
    return ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows) + "\n").encode()


class QuestionPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def make_package(self, root, *, review_status="approved", representation="structured", image_requirement="required",
                     kb_name="图题库", package_id="synthetic-package", document_id="doc-a", question_id="doc-a-q13"):
        root = Path(root)
        (root / "documents").mkdir(parents=True)
        (root / "assets").mkdir()
        image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 40), False)
        image.clear_with(0xEEEEEE)
        image_bytes = image.tobytes("png")
        asset_id = sha(image_bytes)
        if image_requirement == "required":
            (root / "assets" / f"{asset_id}.png").write_bytes(image_bytes)
        document = b"# Synthetic reviewed source\n"
        (root / "documents" / f"{document_id}.md").write_bytes(document)
        body = [{"type": "text", "value": "13. 回旋加速器图题的合成题干足够长，下列说法正确的是"}]
        choices = [f"{label}. 合成选项{label}" for label in "ABCD"]
        assets = [asset_id] if image_requirement == "required" else []
        retrieval_text = "\n".join([*(block["value"] for block in body), *choices])
        actual_body = body if representation == "structured" else []
        actual_choices = choices if representation == "structured" else []
        question = {
            "question_id": question_id, "document_id": document_id, "original_question_number": "13",
            "question_type": "multiple_choice", "page": 3, "order": 13,
            "representation": representation, "body": actual_body,
            "choices": actual_choices, "choice_mode": "multiple", "retrieval_text": retrieval_text,
            "retrieval_text_sha256": sha(retrieval_text.encode()),
            "asset_ids": assets, "body_sha256": sha(jsonl(actual_body)), "choices_sha256": sha(jsonl(actual_choices)),
            "asset_relation_sha256": sha(jsonl(assets)), "review_status": review_status,
            "review": {"reviewed_at": "2026-09-16T00:00:00Z", "reviewed_by": "agent-visual-review",
                       "basis": "synthetic-fixture", "reason": "engineering test only",
                       "image_requirement": image_requirement},
        }
        role = "full_question_facsimile" if representation == "facsimile" else "question_figure"
        asset = {
            "asset_id": asset_id, "path": f"assets/{asset_id}.png", "media_type": "image/png",
            "width": 80, "height": 40, "bytes": len(image_bytes), "question_id": question_id,
            "role": role, "required": True, "order": 1, "placement": "replace_question" if representation == "facsimile" else "after_stem",
            "source_document_id": document_id, "source_page": 3, "source_bbox": [100, 100, 200, 200],
            "source_document_sha256": "a" * 64, "input_sha256": asset_id, "runtime_sha256": asset_id,
            "review_status": review_status, "review_basis": "synthetic-fixture",
        }
        asset_rows = [asset] if image_requirement == "required" else []
        questions_raw, assets_raw = jsonl([question]), jsonl(asset_rows) if asset_rows else b""
        (root / "questions.jsonl").write_bytes(questions_raw)
        (root / "asset-manifest.jsonl").write_bytes(assets_raw)
        package = {
            "schema_version": "1.0", "package_id": package_id, "kb_name": kb_name,
            "source_type": "pdf-derived", "documents": [{"document_id": document_id, "title": "合成试卷",
                "content_sha256": sha(document), "source_document": {"identifier": "synthetic.pdf", "sha256": "a" * 64, "pages": 3}}],
            "adapter": {"name": "synthetic", "version": "1", "created_at": "2026-09-16T00:00:00Z"},
            "hashes": {"questions_sha256": sha(questions_raw), "asset_manifest_sha256": sha(assets_raw),
                       "assets_sha256": sha(jsonl([asset_id]) if image_requirement == "required" else b"\n")},
            "scope": "source and image relation review; not physics, teacher, print or teaching acceptance",
        }
        (root / "package.json").write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
        sums = []
        for path in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
            sums.append(f"{sha(path.read_bytes())}  {path.relative_to(root).as_posix()}")
        (root / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
        return question, asset

    def test_approved_structured_question_without_images_uses_same_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            question, _ = self.make_package(directory, image_requirement="none")
            store = self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库")
            page = store.catalog({"limit": 20})
            self.assertEqual(page["eligible"][0]["asset_count"], 0)
            resolved = store.resolve({"question_id": question["question_id"],
                                      "source_excerpt": "回旋加速器图题的合成题干"})
            self.assertEqual(resolved["assets"], [])

    def test_approved_structured_and_facsimile_resolve_through_small_interface(self):
        for representation in ("structured", "facsimile"):
            with self.subTest(representation=representation), tempfile.TemporaryDirectory() as directory:
                question, asset = self.make_package(directory, representation=representation)
                store = self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库")
                page = store.catalog({"limit": 20})
                self.assertEqual(page["eligible"][0]["question_id"], question["question_id"])
                resolved = store.resolve({"question_id": question["question_id"], "source_excerpt": "回旋加速器图题的合成题干"})
                self.assertEqual(resolved["question"]["representation"], representation)
                self.assertEqual(resolved["assets"][0]["asset_id"], asset["asset_id"])

    def test_pending_missing_or_orphan_assets_fail_closed(self):
        cases = []
        with tempfile.TemporaryDirectory() as directory:
            self.make_package(directory, review_status="pending")
            cases.append((directory, "question-review-not-approved"))
            with self.assertRaisesRegex(self.module.QuestionPackageError, cases[-1][1]):
                self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库")
        with tempfile.TemporaryDirectory() as directory:
            _, asset = self.make_package(directory)
            (Path(directory) / asset["path"]).unlink()
            with self.assertRaisesRegex(self.module.QuestionPackageError, "package-file-set-drift|asset-missing|required-file"):
                self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库")
        with tempfile.TemporaryDirectory() as directory:
            self.make_package(directory)
            rows = (Path(directory) / "asset-manifest.jsonl").read_text(encoding="utf-8").splitlines()
            orphan = json.loads(rows[0]); orphan["asset_id"] = "b" * 64; orphan["path"] = "assets/" + "b" * 64 + ".png"; orphan["runtime_sha256"] = "b" * 64
            (Path(directory) / "asset-manifest.jsonl").write_bytes(jsonl([json.loads(rows[0]), orphan]))
            with self.assertRaisesRegex(self.module.QuestionPackageError, "package-sha256sums-drift|orphan"):
                self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库")

    def test_paths_hashes_media_and_review_matrix_fail_closed(self):
        mutations = [
            (lambda q, a, r: a.update(path="../escape.png"), "asset-path"),
            (lambda q, a, r: a.update(path="C:/escape.png"), "asset-path"),
            (lambda q, a, r: a.update(path="https://example.invalid/a.png"), "asset-path"),
            (lambda q, a, r: a.update(path="data:image/png;base64,AA=="), "asset-path"),
            (lambda q, a, r: a.update(media_type="image/svg+xml"), "asset-media-type"),
            (lambda q, a, r: a.update(width=50000), "asset-pixel-limit"),
            (lambda q, a, r: a.update(bytes=20 * 1024 * 1024), "asset-byte-limit"),
            (lambda q, a, r: a.update(question_id="other-question"), "asset-question-mismatch|orphan"),
            (lambda q, a, r: q.update(asset_ids=[]), "required-asset|orphan"),
        ]
        for mutate, error in mutations:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                question, asset = self.make_package(directory)
                mutate(question, asset, Path(directory))
                (Path(directory) / "questions.jsonl").write_bytes(jsonl([question]))
                (Path(directory) / "asset-manifest.jsonl").write_bytes(jsonl([asset]))
                with self.assertRaisesRegex(self.module.QuestionPackageError, error):
                    self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库", verify_sums=False)

    def test_image_requirement_must_match_asset_relation(self):
        with tempfile.TemporaryDirectory() as directory:
            question, asset = self.make_package(directory)
            question["review"]["image_requirement"] = "none"
            (Path(directory) / "questions.jsonl").write_bytes(jsonl([question]))
            with self.assertRaisesRegex(self.module.QuestionPackageError, "image-requirement"):
                self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库", verify_sums=False)

    def test_duplicate_ids_corrupt_fake_extension_and_hash_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            question, _ = self.make_package(directory)
            (Path(directory) / "questions.jsonl").write_bytes(jsonl([question, question]))
            with self.assertRaisesRegex(self.module.QuestionPackageError, "duplicate-question"):
                self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库", verify_sums=False)
        with tempfile.TemporaryDirectory() as directory:
            _, asset = self.make_package(directory)
            (Path(directory) / asset["path"]).write_bytes(b"not an image")
            with self.assertRaisesRegex(self.module.QuestionPackageError, "asset-(hash|decode|media)"):
                self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库", verify_sums=False)
        with tempfile.TemporaryDirectory() as directory:
            self.make_package(directory)
            (Path(directory) / "documents/doc-a.md").write_bytes(b"drift")
            with self.assertRaisesRegex(self.module.QuestionPackageError, "package-sha256sums-drift"):
                self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库")

    def test_legacy_review_without_explicit_image_requirement_remains_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            question, _ = self.make_package(directory)
            del question["review"]["image_requirement"]
            (Path(directory) / "questions.jsonl").write_bytes(jsonl([question]))
            store = self.module.VerifiedQuestionStore(Path(directory), expected_kb_name="图题库", verify_sums=False)
            self.assertEqual(store.resolve({"question_id": question["question_id"],
                                            "source_excerpt": "回旋加速器图题的合成题干"})["question"]["question_id"],
                             question["question_id"])


if __name__ == "__main__":
    unittest.main()
