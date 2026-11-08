"""Fail-closed registry for immutable, administrator-published source providers.

The caller supplies only an exact knowledge-base name.  Filesystem paths are
derived from the registry and validated against the revision's SHA256SUMS.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re


DEFAULT_ROOT = Path("private/legacy/providers")
MAX_REGISTRY = 1024 * 1024
MAX_PROVIDER = 1024 * 1024
MAX_FILES = 10000
MAX_REVISION_BYTES = 256 * 1024 * 1024
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
REVISION = re.compile(r"rev-[a-f0-9]{12,64}")
SHA256 = re.compile(r"[a-f0-9]{64}")


def _question_package_module():
    from . import package
    return package


class ProviderRegistryError(ValueError):
    pass


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path, maximum: int) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise ProviderRegistryError("provider-required-file-unavailable:" + path.name)
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > maximum:
            raise ProviderRegistryError("provider-file-size-limit:" + path.name)
        # Reading maximum+1 allocated 256 MiB even for a tiny manifest row's
        # file on Windows. Bind the read to this open file's actual size;
        # reject growth/shrinkage rather than silently hashing a prefix.
        data = handle.read(size + 1)
        if len(data) != size or os.fstat(handle.fileno()).st_size != size:
            raise ProviderRegistryError("provider-file-changed-during-read:" + path.name)
    if len(data) > maximum:
        raise ProviderRegistryError("provider-file-size-limit:" + path.name)
    return data


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProviderRegistryError("provider-duplicate-json-key:" + key)
        result[key] = value
    return result


def _read_json(path: Path, maximum: int) -> tuple[dict, str]:
    raw = _read_bytes(path, maximum)
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProviderRegistryError("provider-invalid-json:" + path.name) from exc
    if not isinstance(value, dict):
        raise ProviderRegistryError("provider-json-object-required:" + path.name)
    return value, _digest(raw)


def _confined(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ProviderRegistryError("provider-invalid-relative-path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ProviderRegistryError("provider-path-escape")
    cursor = root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ProviderRegistryError("provider-symlink-forbidden")
    try:
        cursor.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ProviderRegistryError("provider-path-escape") from exc
    return cursor


@dataclass(frozen=True)
class ProviderRevision:
    kb_name: str
    provider_id: str
    revision_id: str
    provider_fingerprint: str
    root: Path
    registry_sha256: str
    manifest: dict

    @property
    def knowledge_root(self) -> Path:
        return self.root / "knowledge"

    @property
    def review_path(self) -> Path:
        return self.root / "source-review.json"

    @property
    def question_package_root(self) -> Path:
        return self.root / "question-package"

    @property
    def binding(self) -> dict:
        if self.manifest["adapter_kind"] != "deeptutor-llamaindex-v1":
            raise ProviderRegistryError("provider-legacy-binding-unavailable")
        hashes = self.manifest["hashes"]
        return {
            "schema_version": "1.0",
            "kb_name": self.kb_name,
            "mount_root": str(self.knowledge_root),
            "embedding_signature": self.manifest["embedding_signature"],
            "verified_active_version": self.manifest["verified_active_version"],
            "review_manifest_path": str(self.review_path),
            "review_manifest_sha256": hashes["review_manifest_sha256"],
        }

    def evidence(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "revision_id": self.revision_id,
            "provider_fingerprint": self.provider_fingerprint,
            "adapter_kind": self.manifest["adapter_kind"],
        }


class SourceProviderRegistry:
    """Resolve one exact KB name to one verified immutable revision."""

    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)

    def _registry(self) -> tuple[dict, str]:
        if self.root.is_symlink():
            raise ProviderRegistryError("provider-root-symlink-forbidden")
        root = self.root.resolve(strict=True)
        registry, fingerprint = _read_json(root / "registry.json", MAX_REGISTRY)
        if set(registry) != {"schema_version", "providers"} or registry["schema_version"] != "1.0":
            raise ProviderRegistryError("provider-registry-schema-incompatible")
        if not isinstance(registry["providers"], list) or len(registry["providers"]) > 1000:
            raise ProviderRegistryError("provider-registry-size-limit")
        return registry, fingerprint

    def _entry(self, kb_name: str) -> tuple[dict, str]:
        if not isinstance(kb_name, str) or not kb_name or len(kb_name) > 300:
            raise ProviderRegistryError("knowledge-base-name-invalid")
        registry, registry_hash = self._registry()
        matches = [entry for entry in registry["providers"]
                   if isinstance(entry, dict) and entry.get("kb_name") == kb_name]
        if not matches:
            raise ProviderRegistryError("knowledge-base-provider-not-registered")
        if len(matches) != 1:
            raise ProviderRegistryError("knowledge-base-provider-duplicate-mapping")
        entry = matches[0]
        required = {"kb_name", "provider_id", "active_revision", "provider_fingerprint", "status"}
        if set(entry) != required:
            raise ProviderRegistryError("provider-registry-entry-invalid")
        if entry["status"] != "active":
            raise ProviderRegistryError("knowledge-base-provider-not-active")
        if not ID.fullmatch(str(entry["provider_id"])) or not REVISION.fullmatch(str(entry["active_revision"])):
            raise ProviderRegistryError("provider-registry-entry-invalid")
        if not SHA256.fullmatch(str(entry["provider_fingerprint"])):
            raise ProviderRegistryError("provider-registry-entry-invalid")
        return entry, registry_hash

    def resolve(self, kb_name: str) -> ProviderRevision:
        entry, registry_hash = self._entry(kb_name)
        revision = _confined(
            self.root.resolve(),
            f"revisions/{entry['provider_id']}/{entry['active_revision']}",
        )
        if not revision.is_dir():
            raise ProviderRegistryError("provider-revision-unavailable")
        manifest = self.verify_revision(revision, expected=entry)
        return ProviderRevision(
            kb_name=kb_name,
            provider_id=entry["provider_id"],
            revision_id=entry["active_revision"],
            provider_fingerprint=entry["provider_fingerprint"],
            root=revision,
            registry_sha256=registry_hash,
            manifest=manifest,
        )

    def resolve_revision(self, provider_id: str, revision_id: str) -> ProviderRevision:
        if not ID.fullmatch(provider_id) or not REVISION.fullmatch(revision_id):
            raise ProviderRegistryError("provider-revision-identifier-invalid")
        revision = _confined(self.root.resolve(strict=True), f"revisions/{provider_id}/{revision_id}")
        manifest = self.verify_revision(revision)
        return ProviderRevision(
            kb_name=manifest["kb_name"], provider_id=provider_id,
            revision_id=revision_id,
            provider_fingerprint=_digest(_read_bytes(revision / "SHA256SUMS", MAX_PROVIDER)),
            root=revision, registry_sha256="unpublished", manifest=manifest,
        )

    def ensure_current(self, provider: ProviderRevision) -> None:
        current = self.resolve(provider.kb_name)
        if (current.registry_sha256 != provider.registry_sha256
                or current.provider_id != provider.provider_id
                or current.revision_id != provider.revision_id
                or current.provider_fingerprint != provider.provider_fingerprint):
            raise ProviderRegistryError("provider-revision-changed-during-read")

    def verify_revision(self, revision: Path, expected: dict | None = None) -> dict:
        if not revision.is_dir() or revision.is_symlink():
            raise ProviderRegistryError("provider-revision-unavailable")
        sums_raw = _read_bytes(revision / "SHA256SUMS", MAX_PROVIDER)
        fingerprint = _digest(sums_raw)
        if expected is not None and fingerprint != expected["provider_fingerprint"]:
            raise ProviderRegistryError("provider-fingerprint-drift")
        try:
            lines = sums_raw.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError as exc:
            raise ProviderRegistryError("provider-sha256sums-invalid") from exc
        if not 1 <= len(lines) <= MAX_FILES:
            raise ProviderRegistryError("provider-manifest-size-limit")
        declared, total = {}, 0
        for line in lines:
            match = re.fullmatch(r"([a-f0-9]{64})  (.+)", line)
            if not match or match.group(2) == "SHA256SUMS" or match.group(2) in declared:
                raise ProviderRegistryError("provider-sha256sums-invalid")
            path = _confined(revision, match.group(2))
            data = _read_bytes(path, MAX_REVISION_BYTES)
            total += len(data)
            if total > MAX_REVISION_BYTES or _digest(data) != match.group(1):
                raise ProviderRegistryError("provider-revision-hash-drift:" + match.group(2))
            declared[match.group(2)] = match.group(1)
        actual = {
            path.relative_to(revision).as_posix()
            for path in revision.rglob("*")
            if path.is_file() and path.relative_to(revision).as_posix() != "SHA256SUMS"
        }
        if actual != set(declared):
            raise ProviderRegistryError("provider-revision-file-set-drift")
        provider, _ = _read_json(revision / "provider.json", MAX_PROVIDER)
        required = {
            "schema_version", "provider_id", "revision_id", "adapter_kind", "kb_name",
            "deeptutor_version", "rag_provider", "embedding_signature",
            "verified_active_version", "created_at", "previous_revision_id", "scope", "hashes",
        }
        if set(provider) != required or provider["schema_version"] != "1.0":
            raise ProviderRegistryError("provider-schema-incompatible")
        if provider["adapter_kind"] not in {"deeptutor-llamaindex-v1", "question-package-v1"} or provider["rag_provider"] != "llamaindex":
            raise ProviderRegistryError("provider-adapter-incompatible")
        if provider["provider_id"] != revision.parent.name or provider["revision_id"] != revision.name:
            raise ProviderRegistryError("provider-revision-identity-mismatch")
        if expected is not None and (provider["provider_id"] != expected["provider_id"]
                                     or provider["revision_id"] != expected["active_revision"]
                                     or provider["kb_name"] != expected["kb_name"]):
            raise ProviderRegistryError("provider-registry-revision-mismatch")
        if not re.fullmatch(r"[a-f0-9]{16}", str(provider["embedding_signature"])):
            raise ProviderRegistryError("provider-embedding-signature-invalid")
        hashes = provider["hashes"]
        common_hash_fields = {
            "kb_metadata_sha256": "knowledge/metadata.json",
            "index_meta_sha256": f"knowledge/{provider['verified_active_version']}/meta.json",
            "docstore_sha256": f"knowledge/{provider['verified_active_version']}/docstore.json",
            "catalog_sha256": "catalog.jsonl",
            "raw_manifest_sha256": "raw-manifest.json",
        }
        if provider["adapter_kind"] == "deeptutor-llamaindex-v1":
            hash_fields = common_hash_fields | {"review_manifest_sha256": "source-review.json"}
            if isinstance(hashes, dict) and "review_provenance_sha256" in hashes:
                hash_fields["review_provenance_sha256"] = "review-provenance.json"
        else:
            hash_fields = common_hash_fields | {
                "question_package_sums_sha256": "question-package/SHA256SUMS",
                "question_package_json_sha256": "question-package/package.json",
                "questions_sha256": "question-package/questions.jsonl",
                "asset_manifest_sha256": "question-package/asset-manifest.jsonl",
            }
        if not isinstance(hashes, dict) or set(hashes) != set(hash_fields):
            raise ProviderRegistryError("provider-hash-manifest-invalid")
        for field, relative in hash_fields.items():
            if hashes[field] != declared.get(relative):
                raise ProviderRegistryError("provider-declared-hash-mismatch:" + field)
        if provider["adapter_kind"] == "question-package-v1":
            try:
                _question_package_module().VerifiedQuestionStore(
                    revision / "question-package", expected_kb_name=provider["kb_name"]
                )
            except Exception as exc:
                raise ProviderRegistryError("provider-question-package-invalid:" + str(exc).split(":", 1)[0]) from exc
        return provider


__all__ = [
    "DEFAULT_ROOT", "ProviderRegistryError", "ProviderRevision", "SourceProviderRegistry",
]
