"""Validate and administer immutable original-answer packages outside the provider registry.

Operator entry point (with the extension installed):
    python adapters/deeptutor/answer_package.py --providers-root ROOT --kb-name KB ...
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

import pymupdf as fitz
from jsonschema import Draft202012Validator

from ..render.contract import ContractError, _answer_blocks, source_choice_labels
from .tags import question_fingerprint
from ..errors import ServiceError as NativeExportError
from . import package as package_io_module


EXTENSION = Path(__file__).resolve().parents[1] / "schemas"
PACKAGE_FILES = {"package.json", "answers.jsonl", "asset-manifest.jsonl"}
ASSET_PATH = re.compile(r"assets/[a-f0-9]{64}\.png")
PACKAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _fail(code):
    raise NativeExportError("native-answer-package-" + code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


@lru_cache(maxsize=1)
def _validators():
    schema = json.loads((EXTENSION / "answer-package.schema.json").read_text(encoding="utf-8"))
    return {"package": Draft202012Validator(schema), **{
        name: Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/" + name})
        for name in ("answer", "asset")
    }}


def _validate(name, value):
    if not _validators()[name].is_valid(value):
        _fail(name + "-schema-invalid")


def _bbox_valid(bbox):
    x0, y0, x1, y1 = bbox
    try:
        return all(math.isfinite(v) for v in bbox) and 0 <= x0 < x1 and 0 <= y0 < y1
    except OverflowError:
        return False


def _mode_map(tags):
    return {row["question_id"]: row["choice_mode"] for row in tags["questions"]} if tags and tags["schema_version"] == 2 else {}


def _directory(settings, provider):
    parent = settings.providers_root.parent / "question-answers"
    key = _sha(provider.kb_name.encode())[:24]
    directory = parent / key
    for path in (parent, directory):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            _fail("root-invalid")
    return directory


class VerifiedAnswerPackage:
    def __init__(self, root, provider, store, modes=None, *, package_io=None):
        self.root = Path(root)
        self.package_io = package_io or package_io_module
        try:
            self._load(provider, store, modes or {})
        except self.package_io.QuestionPackageError as exc:
            raise NativeExportError("native-answer-package-" + str(exc)) from exc

    def _files(self):
        io = self.package_io
        if self.root.is_symlink() or not self.root.is_dir():
            _fail("directory-invalid")
        sums = io._read(self.root / "SHA256SUMS")
        files, total = {}, len(sums)
        try:
            lines = sums.decode("ascii").splitlines()
        except UnicodeDecodeError:
            _fail("sums-invalid")
        if not 3 <= len(lines) <= io.MAX_RECORDS + len(PACKAGE_FILES):
            _fail("sums-invalid")
        for line in lines:
            digest, sep, name = line.partition("  ")
            if (not sep or not io.SHA256.fullmatch(digest) or name in files
                    or not (name in PACKAGE_FILES or ASSET_PATH.fullmatch(name))):
                _fail("sums-invalid")
            data = io._read(io._relative(self.root, name), io.MAX_IMAGE_BYTES if name.startswith("assets/") else io.MAX_FILE_BYTES)
            total += len(data)
            if total > io.MAX_PACKAGE_BYTES:
                _fail("total-size-limit")
            if _sha(data) != digest:
                _fail("hash-drift")
            files[name] = data
        actual = set()
        for path in self.root.iterdir():
            if path.is_symlink():
                _fail("symlink-forbidden")
            if path.name == "assets" and path.is_dir():
                for asset in path.iterdir():
                    if asset.is_symlink() or not asset.is_file():
                        _fail("asset-file-invalid")
                    actual.add("assets/" + asset.name)
            elif path.is_file():
                actual.add(path.name)
            else:
                _fail("file-set-drift")
        if actual != set(files) | {"SHA256SUMS"} or not PACKAGE_FILES <= files.keys():
            _fail("file-set-drift")
        return files

    def _load(self, provider, store, modes):
        io = self.package_io
        files = self._files()
        raw = files["package.json"]
        package = io._json(raw, "package.json")
        _validate("package", package)
        if package["answer_package_id"] != self.root.name or package["kb_name"] != provider.kb_name:
            _fail("identity-mismatch")
        if package["provider"] != provider.evidence():
            _fail("provider-drift")
        documents = {}
        document_ids = set()
        for document in package["answer_documents"]:
            doc_id, answer_doc_id = document["document_id"], document["answer_document_id"]
            if doc_id not in store.documents or doc_id in documents or answer_doc_id in document_ids:
                _fail("document-binding-invalid")
            documents[doc_id] = document
            document_ids.add(answer_doc_id)
        rows = io._jsonl(files["answers.jsonl"], "answers.jsonl", allow_empty=True)
        assets = io._jsonl(files["asset-manifest.jsonl"], "asset-manifest.jsonl", allow_empty=True)
        self.answers, self.assets, self.asset_bytes = {}, {}, {}
        for row in rows:
            _validate("answer", row)
            qid = row["question_id"]
            if qid in self.answers or qid not in store.questions:
                _fail("coverage-invalid")
            question = store.questions[qid]
            if (row["question_sha256"] != question_fingerprint(question)
                    or row["document_id"] != question["document_id"]
                    or row["original_question_number"] != question["original_question_number"]):
                _fail("question-drift")
            document = documents.get(row["document_id"])
            if (document is None or max(row["source_pages"]) > document["pages"]
                    or any(region["page"] not in row["source_pages"] or not _bbox_valid(region["bbox"])
                           for region in row["source_bboxes"])):
                _fail("source-pages-invalid")
            if row["status"] == "source":
                self._key(row, question, modes.get(qid, question["choice_mode"]))
                try:
                    _answer_blocks(row["body"], "answer-package.body")
                except ContractError as exc:
                    raise NativeExportError("native-answer-package-body-invalid") from exc
            self.answers[qid] = row
        if self.answers.keys() != store.questions.keys():
            _fail("coverage-invalid")

        for asset in assets:
            _validate("asset", asset)
            asset_id = asset["asset_id"]
            if (asset_id in self.assets or asset["path"] != f"assets/{asset_id}.png"
                    or asset["question_id"] not in self.answers):
                _fail("asset-identity-invalid")
            data = files.get(asset["path"])
            answer = self.answers[asset["question_id"]]
            document = documents[answer["document_id"]]
            if (data is None or len(data) != asset["bytes"] or _sha(data) != asset_id
                    or asset["runtime_sha256"] != asset_id):
                _fail("asset-hash-drift")
            if (not 5 <= asset.get("width_mm", 100) <= 170
                    or asset["width"] * asset["height"] > io.MAX_IMAGE_PIXELS
                    or max(asset["width"] / asset["height"], asset["height"] / asset["width"]) > 20
                    or io._image_info(data) != ("image/png", asset["width"], asset["height"])):
                _fail("asset-dimensions-invalid")
            try:
                decoded = fitz.Pixmap(data)
            except (RuntimeError, ValueError) as exc:
                raise NativeExportError("native-answer-package-asset-decode-failed") from exc
            if (decoded.width, decoded.height) != (asset["width"], asset["height"]):
                _fail("asset-dimensions-invalid")
            if (asset["source_document_id"] != document["answer_document_id"]
                    or asset["source_document_sha256"] != document["sha256"]
                    or asset["source_page"] not in answer["source_pages"] or not _bbox_valid(asset["source_bbox"])):
                _fail("asset-source-invalid")
            self.assets[asset_id] = asset
            self.asset_bytes[asset_id] = data
        referenced = set()
        for qid, answer in self.answers.items():
            figures = [block["asset_id"] for block in answer["body"] if block["type"] == "figure"]
            if len(figures) > 12:
                _fail("asset-count-limit")
            for order, asset_id in enumerate(figures, 1):
                asset = self.assets.get(asset_id)
                if (asset is None or asset_id in referenced or asset["question_id"] != qid or asset["order"] != order):
                    _fail("figure-reference-invalid")
                referenced.add(asset_id)
        if referenced != self.assets.keys() or {a["path"] for a in assets} != files.keys() - PACKAGE_FILES:
            _fail("asset-set-drift")
        if package["hashes"] != {
            "answers_sha256": _sha(files["answers.jsonl"]),
            "asset_manifest_sha256": _sha(files["asset-manifest.jsonl"]),
            "assets_sha256": io._hash_rows(sorted(self.assets)),
        }:
            _fail("declared-hash-drift")
        self.asset_manifest_bytes = files["asset-manifest.jsonl"]
        self.manifest = package
        self.sha256 = _sha(raw)
        self.documents = documents

    @staticmethod
    def _key(row, question, mode):
        key = row["key"]
        if question["choices"] or mode in {"single", "multiple"}:
            options = source_choice_labels(question, mode)
            if options and key is None and row["body"]:
                return
            if not key or key != "".join(sorted(set(key))) or not set(key) <= options or (mode == "single" and len(key) != 1):
                _fail("key-invalid")
        elif key is not None or not row["body"]:
            _fail("key-or-body-invalid")

    def evidence(self):
        return {"answer_package_id": self.manifest["answer_package_id"], "package_sha256": self.sha256}

