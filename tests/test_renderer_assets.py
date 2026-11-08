import hashlib
from pathlib import Path
import tempfile
import unittest

import pymupdf as fitz

from physics_worksheet_mcp.render.contract import ContractError, validate_spec
from physics_worksheet_mcp.render.renderer import _tex, render_exam
from test_contract import valid_spec
from test_contract_assets import ExamSpec11ContractTests
from test_renderer import FakeCompiler


class MemoryAssetReader:
    def __init__(self, values):
        self.values = values

    def read(self, asset_id):
        if asset_id not in self.values:
            raise ContractError("verified-asset-not-declared")
        return self.values[asset_id]


def png_bytes(width=120, height=60):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pix.clear_with(0xDDDDDD)
    return pix.tobytes("png")


class RendererAssetTests(unittest.TestCase):
    def spec(self, representation="structured", count=1):
        raw = ExamSpec11ContractTests().spec11(representation)
        assets = []
        values = {}
        for index in range(count):
            data = png_bytes(120 + index * 10, 60)
            asset_id = hashlib.sha256(data).hexdigest()
            assets.append({"asset_id": asset_id, "role": "question_figure" if representation == "structured" else "full_question_facsimile",
                           "required": True, "order": index + 1,
                           "placement": "after_stem" if representation == "structured" else "replace_question"})
            values[asset_id] = {"bytes": data, "media_type": "image/png", "width": 120 + index * 10, "height": 60}
        raw["questions"][0]["assets"] = assets
        return raw, values

    def test_structured_single_and_multi_assets_have_deterministic_order(self):
        raw, values = self.spec(count=2)
        spec, _ = validate_spec(raw)
        tex = _tex(spec, asset_files={asset_id: f"verified-assets/{asset_id}.png" for asset_id in values})
        positions = [tex.index(asset_id) for asset_id in values]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(r"\includegraphics", tex)
        self.assertIn(r"width=.55\linewidth,height=.32\textheight", tex)

    def test_structured_no_image_question_renders_without_graphics(self):
        raw, _ = self.spec(count=0)
        spec, _ = validate_spec(raw)
        tex = _tex(spec, asset_files={})
        self.assertNotIn(r"\includegraphics", tex)
        self.assertNotIn(r"\usepackage{graphicx}", tex)

    def test_legacy_10_tex_does_not_load_image_package(self):
        spec, _ = validate_spec(valid_spec())
        self.assertNotIn("graphicx", _tex(spec))

    def test_facsimile_replaces_structured_body_and_keeps_source(self):
        raw, values = self.spec("facsimile")
        spec, _ = validate_spec(raw)
        tex = _tex(spec, asset_files={next(iter(values)): "verified-assets/facsimile.png"})
        self.assertIn("facsimile.png", tex)
        self.assertIn("来源：", tex)
        self.assertNotIn("物体做匀速直线运动", tex)
        self.assertIn(r"width=\linewidth,height=.72\textheight", tex)

    def test_multi_crop_facsimile_keeps_order_and_allows_page_breaks_between_images(self):
        raw, values = self.spec("facsimile", count=3)
        spec, _ = validate_spec(raw)
        asset_files = {asset_id: f"verified-assets/{asset_id}.png" for asset_id in values}
        tex = _tex(spec, asset_files=asset_files)
        positions = [tex.index(asset_id) for asset_id in values]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(r"height=.72\textheight", tex)
        self.assertNotIn(r"\par\nobreak\begin{center}", tex)

    def test_missing_corrupt_hash_or_extreme_aspect_asset_never_reaches_compiler(self):
        raw, values = self.spec()
        asset_id = next(iter(values))
        for reader, error in (
            (MemoryAssetReader({}), "not-declared"),
            (MemoryAssetReader({asset_id: {**values[asset_id], "bytes": b"broken"}}), "decode|hash"),
            (MemoryAssetReader({asset_id: {**values[asset_id], "bytes": png_bytes(5000, 20), "width": 5000, "height": 20}}), "readable|aspect|pixel|hash"),
        ):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                compiler = FakeCompiler()
                result = render_exam(raw, directory, compiler=compiler, asset_reader=reader)
                self.assertEqual(result.status, "failed")
                self.assertEqual(compiler.calls, 0)
                self.assertRegex(result.error, error)


if __name__ == "__main__":
    unittest.main()
