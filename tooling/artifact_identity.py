"""Reviewer 001: bind an extension source request to a pinned distribution.

No prefix search and no directory-derived package identity.  This prospective
registry is a signed input; sealed feasibility/repair instruments remain sealed.
Only archive data is inspected.  Candidate setup code is never imported.
"""
import hashlib
import json
import re
import tarfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

REGISTRY_REL = "extension/freeze/artifact_identity_registry_20261016.json"


class ArtifactIdentityError(ValueError):
    pass


def canonical_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def expected_entry(root, artifact_root, requested):
    root, artifact_root = Path(root).resolve(), Path(artifact_root).resolve()
    if artifact_root != root / "admission_artifacts":
        raise ArtifactIdentityError("artifact_root_outside_prospective_extension_scope")
    requested = str(requested)
    if not requested or Path(requested).name != requested or requested in (".", ".."):
        raise ArtifactIdentityError("invalid_artifact_request")
    registry = json.loads((root / REGISTRY_REL).read_text(encoding="utf-8"))
    if registry.get("schema_version") != "pyhost-context/artifact-identity-registry/1.0":
        raise ArtifactIdentityError("artifact_registry_schema")
    matches = []
    for key, entry in registry["artifacts"].items():
        # Source-dir strings are compared exactly with independently registered
        # fields.  In particular, an unavailable long name never falls back.
        if requested not in (key, entry.get("source_dir")):
            continue
        if entry.get("project") != key or entry.get("directory") != f"admission_artifacts/{key}":
            raise ArtifactIdentityError("artifact_registry_identity_inconsistent")
        if entry.get("source_dir") != f"{key}-{entry.get('version')}":
            raise ArtifactIdentityError("artifact_registry_source_dir_inconsistent")
        matches.append(entry)
    if len(matches) != 1:
        raise ArtifactIdentityError(f"artifact_request_not_uniquely_registered:{requested}")
    return matches[0]


def identify_sdist(root, artifact_root, requested):
    root, artifact_root = Path(root).resolve(), Path(artifact_root).resolve()
    entry = expected_entry(root, artifact_root, requested)
    directory = artifact_root / entry["project"]
    filename, expected_hash = entry["sdist"]["filename"], entry["sdist"]["sha256"]
    if Path(filename).name != filename or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ArtifactIdentityError("invalid_registered_sdist")
    path = directory / filename
    if not path.is_file():
        raise ArtifactIdentityError(f"registered_sdist_missing:{entry['project']}:{filename}")
    if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory:
        raise ArtifactIdentityError("artifact_path_identity_mismatch")
    raw = path.read_bytes()
    got_hash = hashlib.sha256(raw).hexdigest()
    if got_hash != expected_hash:
        raise ArtifactIdentityError(f"sdist_sha256_mismatch:{entry['project']}")
    expected_size = entry["sdist"].get("bytes")
    if expected_size is not None and len(raw) != expected_size:
        raise ArtifactIdentityError("sdist_size_mismatch")
    with tarfile.open(path, mode="r:gz") as archive:
        metadata = [m for m in archive.getmembers()
                    if m.isfile() and len(PurePosixPath(m.name).parts) == 2
                    and PurePosixPath(m.name).name == "PKG-INFO"]
        if len(metadata) != 1:
            raise ArtifactIdentityError("sdist_top_level_pkg_info_not_unique")
        info = BytesParser().parsebytes(archive.extractfile(metadata[0]).read())
    if len(info.get_all("Name", [])) != 1 or len(info.get_all("Version", [])) != 1:
        raise ArtifactIdentityError("sdist_pkg_info_identity_not_unique")
    if canonical_name(info["Name"]) != canonical_name(entry["project"]):
        raise ArtifactIdentityError("sdist_pkg_info_project_mismatch")
    if info["Version"].strip() != str(entry["version"]):
        raise ArtifactIdentityError("sdist_pkg_info_version_mismatch")
    return {
        "request": str(requested), "project": entry["project"],
        "version": entry["version"], "source_dir": entry["source_dir"],
        "artifact_path": str(path.relative_to(root)),
        "expected_sha256": expected_hash, "observed_sha256": got_hash,
        "bytes": len(raw), "metadata_member": metadata[0].name,
        "metadata_name": info["Name"], "metadata_version": info["Version"],
        "registry_path": REGISTRY_REL,
        "registry_sha256": hashlib.sha256((root / REGISTRY_REL).read_bytes()).hexdigest(),
        "identity_verified": True,
    }


def selected_sdist(root, artifact_root, requested):
    receipt = identify_sdist(root, artifact_root, requested)
    return Path(root) / receipt["artifact_path"]
