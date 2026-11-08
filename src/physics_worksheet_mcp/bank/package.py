"""Verified Question Package 1.0 deep Module.

The external Interface is intentionally limited to ``catalog`` and ``resolve``.
MinerU paths, package layout, hashes, image headers and review records remain
inside this implementation.  The module uses only the Python standard library
so it can run in the restricted source-adapter mount.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import struct


SHA256 = re.compile(r"[a-f0-9]{64}")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_SIDE = 10_000
MAX_RECORDS = 5000


class QuestionPackageError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise QuestionPackageError("package-required-file-unavailable:" + path.name)
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > maximum:
            raise QuestionPackageError("package-file-size-limit:" + path.name)
        value = handle.read(size + 1)
        if len(value) != size or os.fstat(handle.fileno()).st_size != size:
            raise QuestionPackageError("package-file-changed-during-read:" + path.name)
    if len(value) > maximum:
        raise QuestionPackageError("package-file-size-limit:" + path.name)
    return value


def _pairs(rows):
    result = {}
    for key, value in rows:
        if key in result:
            raise QuestionPackageError("package-duplicate-json-key:" + key)
        result[key] = value
    return result


def _json(data: bytes, label: str):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise QuestionPackageError("package-invalid-json:" + label) from exc


def _jsonl(data: bytes, label: str, *, allow_empty: bool = False) -> list[dict]:
    rows = []
    if allow_empty and not data.strip():
        return rows
    try:
        for line in data.decode("utf-8").splitlines():
            if not line.strip():
                raise QuestionPackageError("package-empty-jsonl-line:" + label)
            row = json.loads(line, object_pairs_hook=_pairs)
            if not isinstance(row, dict):
                raise QuestionPackageError("package-jsonl-object-required:" + label)
            rows.append(row)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, QuestionPackageError):
            raise
        raise QuestionPackageError("package-invalid-jsonl:" + label) from exc
    minimum = 0 if allow_empty else 1
    if not minimum <= len(rows) <= MAX_RECORDS:
        raise QuestionPackageError("package-record-count-limit:" + label)
    return rows


def _relative(root: Path, value: str, *, prefix: str | None = None) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or value.startswith(("http:", "https:", "data:")):
        raise QuestionPackageError("asset-path-invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise QuestionPackageError("asset-path-escape")
    if prefix is not None and (not pure.parts or pure.parts[0] != prefix):
        raise QuestionPackageError("asset-path-invalid")
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise QuestionPackageError("asset-symlink-forbidden")
    try:
        cursor.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise QuestionPackageError("asset-path-escape") from exc
    return cursor


def _image_info(data: bytes) -> tuple[str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return "image/png", width, height
    if data.startswith(b"\xff\xd8"):
        cursor = 2
        while cursor + 4 <= len(data):
            if data[cursor] != 0xFF:
                cursor += 1
                continue
            marker = data[cursor + 1]
            cursor += 2
            if marker in {0xD8, 0xD9}:
                continue
            if cursor + 2 > len(data):
                break
            length = struct.unpack(">H", data[cursor:cursor + 2])[0]
            if length < 2 or cursor + length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 7:
                height, width = struct.unpack(">HH", data[cursor + 3:cursor + 7])
                return "image/jpeg", width, height
            cursor += length
    raise QuestionPackageError("asset-decode-failed")


def _hash_rows(values) -> str:
    raw = ("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in values) + "\n").encode()
    return _sha(raw)


class VerifiedQuestionStore:
    """Validate one immutable package, then expose only catalog/resolve."""

    def __init__(self, root: Path | str, *, expected_kb_name: str, verify_sums: bool = True):
        self.root = Path(root)
        if self.root.is_symlink() or not self.root.is_dir():
            raise QuestionPackageError("package-root-unavailable")
        self.root = self.root.resolve()
        if verify_sums:
            self._verify_sums()
        package_raw = _read(self.root / "package.json", 1024 * 1024)
        self.package = _json(package_raw, "package.json")
        required_package = {"schema_version", "package_id", "kb_name", "source_type", "documents", "adapter", "hashes", "scope"}
        if not isinstance(self.package, dict) or set(self.package) != required_package or self.package.get("schema_version") != "1.0":
            raise QuestionPackageError("package-schema-incompatible")
        if self.package.get("kb_name") != expected_kb_name:
            raise QuestionPackageError("package-knowledge-base-mismatch")
        if self.package.get("source_type") not in {"pdf-derived", "structured_bundle"}:
            raise QuestionPackageError("package-source-type-invalid")
        if not IDENTIFIER.fullmatch(str(self.package.get("package_id", ""))):
            raise QuestionPackageError("package-id-invalid")
        self.documents = self._documents()
        questions_raw = _read(self.root / "questions.jsonl", 16 * 1024 * 1024)
        assets_raw = _read(self.root / "asset-manifest.jsonl", 16 * 1024 * 1024)
        hashes = self.package.get("hashes")
        if not isinstance(hashes, dict) or set(hashes) != {"questions_sha256", "asset_manifest_sha256", "assets_sha256"}:
            raise QuestionPackageError("package-hash-manifest-invalid")
        if verify_sums and (hashes["questions_sha256"] != _sha(questions_raw) or hashes["asset_manifest_sha256"] != _sha(assets_raw)):
            raise QuestionPackageError("package-declared-hash-mismatch")
        self.questions = self._questions(_jsonl(questions_raw, "questions.jsonl"))
        self.assets = self._assets(_jsonl(assets_raw, "asset-manifest.jsonl", allow_empty=True))
        if verify_sums and hashes["assets_sha256"] != _hash_rows(sorted(self.assets)):
            raise QuestionPackageError("package-assets-set-hash-mismatch")
        self._relations()

    def _verify_sums(self):
        sums = _read(self.root / "SHA256SUMS", 4 * 1024 * 1024)
        declared = {}
        total = 0
        try:
            lines = sums.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise QuestionPackageError("package-sha256sums-invalid") from exc
        for line in lines:
            match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
            if not match or match.group(2) == "SHA256SUMS" or match.group(2) in declared:
                raise QuestionPackageError("package-sha256sums-invalid")
            path = _relative(self.root, match.group(2))
            data = _read(path, MAX_IMAGE_BYTES if match.group(2).startswith("assets/") else MAX_FILE_BYTES)
            total += len(data)
            if total > MAX_PACKAGE_BYTES or _sha(data) != match.group(1):
                raise QuestionPackageError("package-sha256sums-drift:" + match.group(2))
            declared[match.group(2)] = match.group(1)
        actual = {path.relative_to(self.root).as_posix() for path in self.root.rglob("*") if path.is_file() and path.name != "SHA256SUMS"}
        if actual != set(declared):
            raise QuestionPackageError("package-file-set-drift")

    def _documents(self):
        rows = self.package.get("documents")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise QuestionPackageError("package-documents-invalid")
        result = {}
        for row in rows:
            required = {"document_id", "title", "content_sha256", "source_document"}
            if not isinstance(row, dict) or set(row) != required or not IDENTIFIER.fullmatch(str(row.get("document_id", ""))):
                raise QuestionPackageError("package-document-invalid")
            if row["document_id"] in result:
                raise QuestionPackageError("duplicate-document-id")
            path = _relative(self.root, f"documents/{row['document_id']}.md", prefix="documents")
            if not SHA256.fullmatch(str(row["content_sha256"])) or _sha(_read(path)) != row["content_sha256"]:
                raise QuestionPackageError("document-hash-drift")
            source = row["source_document"]
            if not isinstance(source, dict) or set(source) != {"identifier", "sha256", "pages"} or not SHA256.fullmatch(str(source.get("sha256", ""))):
                raise QuestionPackageError("package-source-document-invalid")
            result[row["document_id"]] = deepcopy(row)
        return result

    def _questions(self, rows):
        required = {"question_id", "document_id", "original_question_number", "question_type", "page", "order", "representation", "body", "choices", "choice_mode", "retrieval_text", "retrieval_text_sha256", "asset_ids", "body_sha256", "choices_sha256", "asset_relation_sha256", "review_status", "review"}
        result = {}
        for row in rows:
            if set(row) not in (required, required | {"layout"}) or not IDENTIFIER.fullmatch(str(row.get("question_id", ""))):
                raise QuestionPackageError("question-schema-invalid")
            if row["question_id"] in result:
                raise QuestionPackageError("duplicate-question-id")
            if row["document_id"] not in self.documents:
                raise QuestionPackageError("question-document-unknown")
            if row["representation"] not in {"structured", "facsimile"}:
                raise QuestionPackageError("question-representation-invalid")
            if row["review_status"] != "approved":
                raise QuestionPackageError("question-review-not-approved:" + row["question_id"])
            review = row.get("review")
            legacy_review_fields = {"reviewed_at", "reviewed_by", "basis", "reason"}
            current_review_fields = legacy_review_fields | {"image_requirement"}
            if (not isinstance(review, dict) or frozenset(review) not in {frozenset(legacy_review_fields), frozenset(current_review_fields)}
                    or ("image_requirement" in review and review["image_requirement"] not in {"none", "required"})
                    or any(not isinstance(review.get(field), str) or not review[field]
                           for field in ("reviewed_at", "reviewed_by", "basis", "reason"))):
                raise QuestionPackageError("question-review-invalid:" + row["question_id"])
            if type(row["page"]) is not int or row["page"] < 1 or type(row["order"]) is not int or row["order"] < 1:
                raise QuestionPackageError("question-location-invalid")
            if not isinstance(row["body"], list) or not isinstance(row["choices"], list) or not isinstance(row["asset_ids"], list):
                raise QuestionPackageError("question-content-invalid")
            if not isinstance(row["retrieval_text"], str) or not 10 <= len(row["retrieval_text"]) <= 30000 or row["retrieval_text_sha256"] != _sha(row["retrieval_text"].encode("utf-8")):
                raise QuestionPackageError("question-retrieval-text-invalid")
            if row["representation"] == "structured" and not row["body"]:
                raise QuestionPackageError("structured-body-required")
            if row["representation"] == "facsimile" and (row["body"] or row["choices"]):
                raise QuestionPackageError("facsimile-must-not-duplicate-structured-content")
            if len(row["asset_ids"]) != len(set(row["asset_ids"])):
                raise QuestionPackageError("required-asset-list-invalid")
            image_requirement = review.get("image_requirement", "required")
            if image_requirement == "required" and not row["asset_ids"]:
                raise QuestionPackageError("required-asset-list-invalid")
            if image_requirement == "none" and row["asset_ids"]:
                raise QuestionPackageError("question-image-requirement-mismatch")
            if row["representation"] == "facsimile" and image_requirement != "required":
                raise QuestionPackageError("question-image-requirement-mismatch")
            if any(not SHA256.fullmatch(str(value)) for value in row["asset_ids"]):
                raise QuestionPackageError("asset-id-invalid")
            if row["body_sha256"] != _hash_rows(row["body"]) or row["choices_sha256"] != _hash_rows(row["choices"]) or row["asset_relation_sha256"] != _hash_rows(row["asset_ids"]):
                raise QuestionPackageError("question-content-hash-drift")
            result[row["question_id"]] = deepcopy(row)
        return result

    def _assets(self, rows):
        required = {"asset_id", "path", "media_type", "width", "height", "bytes", "question_id", "role", "required", "order", "placement", "source_document_id", "source_page", "source_bbox", "source_document_sha256", "input_sha256", "runtime_sha256", "review_status", "review_basis"}
        result = {}
        for row in rows:
            if (set(row) not in (required, required | {"width_mm"}, required | {"width_mm", "source_layout"})
                    or not SHA256.fullmatch(str(row.get("asset_id", "")))):
                raise QuestionPackageError("asset-schema-invalid")
            if row.get("placement") == "in_content":
                width = row.get("width_mm")
                if type(width) not in (int, float) or not math.isfinite(width) or not 5 <= width <= 170:
                    raise QuestionPackageError("asset-physical-width-invalid")
            elif "width_mm" in row:
                raise QuestionPackageError("asset-physical-width-placement-invalid")
            if "source_layout" in row:
                layout = row["source_layout"]
                if not isinstance(layout, dict) or set(layout) != {"page_size_pt", "bbox_pt", "alignment", "row_group", "gap_before_mm", "anchor", "basis"}:
                    raise QuestionPackageError("asset-source-layout-invalid")
                if layout["bbox_pt"] != row["source_bbox"] or layout["alignment"] not in {"left", "center", "right"}:
                    raise QuestionPackageError("asset-source-layout-drift")
            if row["asset_id"] in result:
                raise QuestionPackageError("duplicate-asset-id")
            if row["media_type"] not in {"image/png", "image/jpeg"}:
                raise QuestionPackageError("asset-media-type-forbidden")
            if row["path"] != f"assets/{row['asset_id']}" + (".png" if row["media_type"] == "image/png" else ".jpg"):
                raise QuestionPackageError("asset-path-invalid")
            if type(row["bytes"]) is not int or not 1 <= row["bytes"] <= MAX_IMAGE_BYTES:
                raise QuestionPackageError("asset-byte-limit")
            if type(row["width"]) is not int or type(row["height"]) is not int or not 1 <= row["width"] <= MAX_IMAGE_SIDE or not 1 <= row["height"] <= MAX_IMAGE_SIDE or row["width"] * row["height"] > MAX_IMAGE_PIXELS:
                raise QuestionPackageError("asset-pixel-limit")
            if row["role"] not in {"question_figure", "shared_figure", "full_question_facsimile"} or row["required"] is not True or type(row["order"]) is not int or row["order"] < 1:
                raise QuestionPackageError("asset-relation-invalid")
            if row["review_status"] != "approved":
                raise QuestionPackageError("asset-review-not-approved")
            path = _relative(self.root, row["path"], prefix="assets")
            data = _read(path, MAX_IMAGE_BYTES)
            media, width, height = _image_info(data)
            if _sha(data) != row["asset_id"] or row["runtime_sha256"] != row["asset_id"] or row["bytes"] != len(data):
                raise QuestionPackageError("asset-hash-drift")
            if media != row["media_type"] or (width, height) != (row["width"], row["height"]):
                raise QuestionPackageError("asset-media-or-dimension-mismatch")
            result[row["asset_id"]] = deepcopy(row) | {"resolved_path": str(path)}
        return result

    def _relations(self):
        referenced = set()
        for question in self.questions.values():
            assets = []
            for order, asset_id in enumerate(question["asset_ids"], 1):
                asset = self.assets.get(asset_id)
                if asset is None:
                    raise QuestionPackageError("required-asset-missing")
                if asset["question_id"] != question["question_id"] or asset["source_document_id"] != question["document_id"]:
                    raise QuestionPackageError("asset-question-mismatch")
                if asset["source_document_sha256"] != self.documents[question["document_id"]]["source_document"]["sha256"]:
                    raise QuestionPackageError("asset-source-document-hash-mismatch")
                if asset["order"] != order:
                    raise QuestionPackageError("asset-order-invalid")
                if question["representation"] == "facsimile" and asset["role"] != "full_question_facsimile":
                    raise QuestionPackageError("facsimile-asset-role-invalid")
                if question["representation"] == "structured" and asset["role"] == "full_question_facsimile":
                    raise QuestionPackageError("structured-asset-role-invalid")
                referenced.add(asset_id)
                assets.append(asset)
            if len({asset["order"] for asset in assets}) != len(assets):
                raise QuestionPackageError("duplicate-asset-order")
            references = [block["asset_id"] for blocks in [question["body"], *[c for c in question["choices"] if isinstance(c, list)]]
                          for block in blocks if isinstance(block, dict) and block.get("type") == "figure"]
            if references != [asset["asset_id"] for asset in assets if asset["placement"] == "in_content"]:
                raise QuestionPackageError("asset-content-order-mismatch")
        if referenced != set(self.assets):
            raise QuestionPackageError("orphan-asset-forbidden")

    def catalog(self, request: dict | None = None) -> dict:
        request = request or {}
        if not isinstance(request, dict) or set(request) - {"query", "limit", "cursor"}:
            raise QuestionPackageError("catalog-request-invalid")
        query, limit = request.get("query", ""), request.get("limit", 20)
        if not isinstance(query, str) or len(query) > 2000 or type(limit) is not int or not 1 <= limit <= 50:
            raise QuestionPackageError("catalog-request-invalid")
        rows = []
        for question in sorted(self.questions.values(), key=lambda row: (row["document_id"], row["order"], row["question_id"])):
            document = self.documents[question["document_id"]]
            body_text = "".join(str(block.get("value", "")) for block in question["body"] if isinstance(block, dict))
            search_hint = body_text[:160] if body_text else f"{document['title']} 原第 {question['original_question_number']} 题"
            row = {"question_id": question["question_id"], "title": document["title"], "original_number": question["original_question_number"],
                   "page": question["page"], "representation": question["representation"], "asset_count": len(question["asset_ids"]),
                   "search_hint": search_hint}
            if not query or query.casefold() in (" ".join(str(value) for value in row.values())).casefold():
                rows.append(row)
        offset = 0
        if request.get("cursor"):
            try:
                payload = json.loads(base64.urlsafe_b64decode(request["cursor"] + "=" * (-len(request["cursor"]) % 4)))
                offset = payload["offset"]
            except Exception as exc:
                raise QuestionPackageError("catalog-cursor-invalid") from exc
        page = rows[offset:offset + limit]
        next_offset = offset + len(page)
        cursor = None
        if next_offset < len(rows):
            cursor = base64.urlsafe_b64encode(json.dumps({"offset": next_offset}).encode()).decode().rstrip("=")
        return {"schema_version": "1.0", "eligible_total": len(rows), "eligible": page, "next_cursor": cursor}

    def resolve(self, selection: dict) -> dict:
        if not isinstance(selection, dict) or set(selection) != {"question_id", "source_excerpt"}:
            raise QuestionPackageError("selection-invalid")
        question = self.questions.get(selection.get("question_id"))
        if question is None:
            raise QuestionPackageError("question-id-not-found")
        excerpt = selection.get("source_excerpt")
        if not isinstance(excerpt, str) or not 10 <= len(excerpt) <= 30000:
            raise QuestionPackageError("source-excerpt-length-must-be-10-to-30000")
        if excerpt not in question["retrieval_text"]:
            raise QuestionPackageError("exact-source-not-found")
        document = self.documents[question["document_id"]]
        assets = [deepcopy(self.assets[asset_id]) for asset_id in question["asset_ids"]]
        return {"question": deepcopy(question), "document": deepcopy(document), "assets": assets}


__all__ = ["QuestionPackageError", "VerifiedQuestionStore"]
