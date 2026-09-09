"""Loading, merging, saving and hashing capability artifacts.

``ArtifactStore.load(path, tenant)`` composes the three layers:

    capability YAML  ->  apply tenant.overrides[capability.id] (deep merge)  ->  validate

Override semantics (documented in the Tenant model and REPORT §4):

* mappings deep-merge key by key;
* ``steps`` in an override is a mapping keyed by step id and deep-merges into that step
  (the base artifact's step list is never re-ordered by a tenant);
* any list value (e.g. ``locators``) is *replaced*, not appended — a tenant that relabels a
  control supplies the full locator bundle for it, so the result is reviewable in one place;
  ``target.locators_prepend`` / ``locators_append`` add strategies while keeping the base ones
  (a tenant-specific label tried first, the vendor defaults as fallback);
* the applied patch is recorded on the loaded ``Capability.overrides`` for audit.

Saving enforces the approval rule: if the content hash differs from ``review.artifact_sha256``
the status reverts to ``draft`` and the approval fields are cleared.
"""

from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any

import yaml

from teller.artifact.model import AppProfile, Capability, Tenant


class ArtifactError(Exception):
    pass


def _deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    # lists and scalars: patch replaces
    return copy.deepcopy(patch)


def apply_overrides(raw: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a tenant patch to a raw (pre-validation) capability mapping."""
    if not patch:
        return raw
    out = copy.deepcopy(raw)
    step_patch = patch.get("steps")
    rest = {k: v for k, v in patch.items() if k != "steps"}
    out = _deep_merge(out, rest)
    if step_patch:
        if not isinstance(step_patch, dict):
            raise ArtifactError("override 'steps' must be a mapping keyed by step id")
        by_id = {s.get("id"): i for i, s in enumerate(out.get("steps", []))}
        for sid, sp in step_patch.items():
            if sid not in by_id:
                raise ArtifactError(f"override targets unknown step {sid!r}")
            sp = copy.deepcopy(sp)
            tgt = sp.get("target") if isinstance(sp.get("target"), dict) else None
            base_locs = list((out["steps"][by_id[sid]].get("target") or {}).get("locators") or [])
            if tgt is not None and ("locators_prepend" in tgt or "locators_append" in tgt):
                pre = tgt.pop("locators_prepend", []) or []
                post = tgt.pop("locators_append", []) or []
                tgt["locators"] = list(pre) + (tgt.get("locators") or base_locs) + list(post)
            out["steps"][by_id[sid]] = _deep_merge(out["steps"][by_id[sid]], sp)
    out["overrides"] = copy.deepcopy(patch)
    return out


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ArtifactError(f"file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ArtifactError(f"{p}: expected a mapping at top level")
    return data


def load_tenant(path: str | Path) -> Tenant:
    return Tenant.model_validate(load_yaml(path))


def load_profile(path: str | Path) -> AppProfile:
    return AppProfile.model_validate(load_yaml(path))


def load_capability(path: str | Path, tenant: Tenant | None = None) -> Capability:
    raw = load_yaml(path)
    sv = raw.get("schema_version")
    if sv != 1:
        raise ArtifactError(f"{path}: unsupported schema_version {sv!r} (loader supports 1)")
    if tenant is not None:
        cap_id = (raw.get("capability") or {}).get("id")
        patch = tenant.overrides.get(cap_id, {}) if cap_id else {}
        raw = apply_overrides(raw, patch)
    try:
        return Capability.model_validate(raw)
    except Exception as e:  # pydantic.ValidationError — re-wrapped with the path
        raise ArtifactError(f"{path}: {e}") from e


class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=">")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_presenter)


def to_yaml(cap: Capability) -> str:
    data = cap.model_dump(mode="json", exclude_none=True)
    data.pop("overrides", None)  # tenant patches never persist into the base file
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)


def save_capability(cap: Capability, directory: str | Path) -> Path:
    """Write ``<id>@<version>.yaml``, enforcing the approval/hash rule."""
    r = cap.capability.review
    if cap.capability.status == "approved" and r.artifact_sha256 != cap.content_hash():
        cap = cap.model_copy(deep=True)
        cap.capability.status = "draft"
        cap.capability.review.approved_by = None
        cap.capability.review.approved_at = None
        cap.capability.review.artifact_sha256 = None
    out = Path(directory) / f"{cap.file_stem()}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_yaml(cap), encoding="utf-8")
    return out


def approve(cap: Capability, by: str, now: dt.datetime | None = None) -> Capability:
    now = now or dt.datetime.now(dt.UTC)
    cap = cap.model_copy(deep=True)
    cap.capability.review.approved_by = by
    cap.capability.review.approved_at = now.replace(microsecond=0).isoformat()
    cap.capability.review.artifact_sha256 = cap.content_hash()
    cap.capability.status = "approved"
    return cap


class ArtifactStore:
    """Filesystem-backed store rooted at the repo (capabilities/, apps/, tenants/)."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)

    def tenant(self, name: str) -> Tenant:
        return load_tenant(self.root / "tenants" / f"{name}.yaml")

    def profile(self, name: str) -> AppProfile:
        return load_profile(self.root / "apps" / name / "profile.yaml")

    def load(self, path: str | Path, tenant: str | Tenant | None = None) -> Capability:
        t = self.tenant(tenant) if isinstance(tenant, str) else tenant
        p = Path(path)
        if not p.is_absolute() and not p.exists():
            p = self.root / p
        return load_capability(p, t)

    def save(self, cap: Capability) -> Path:
        return save_capability(cap, self.root / "capabilities")

    def list_capabilities(self) -> list[Path]:
        d = self.root / "capabilities"
        return sorted(d.glob("*.yaml")) if d.exists() else []
