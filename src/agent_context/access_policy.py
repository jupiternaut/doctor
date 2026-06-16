from __future__ import annotations

import fnmatch
import gzip
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import append_jsonl, ensure_dir, read_jsonl, write_text

ACCESS_POLICY_VERSION = "0.1"
ACCESS_AUDIT_VERSION = "0.1"
ACCESS_CONSENT_VERSION = "0.1"
DEFAULT_ACCESS_AUDIT_MAX_BYTES = 5_000_000
DEFAULT_ACCESS_AUDIT_MAX_ROTATED_FILES = 3
DEFAULT_ALLOWED_PROVIDERS = [
    "direct_text",
    "markitdown",
    "metadata_only",
    "project_code_index",
    "session_index",
    "semantic_index",
    "git_project",
    "workflow_doc",
    "codex_session",
    "claude_session",
]
DEFAULT_DENY_PATH_PATTERNS = [
    "*/.ssh/*",
    "*/.gnupg/*",
    "*/Library/Keychains/*",
    "*/.aws/credentials",
    "*/.config/gcloud/*",
    "*/id_rsa",
    "*/id_ed25519",
    "*.pem",
    "*.key",
    "*.p12",
    "*.mobileprovision",
    "*.env",
    "*.env.*",
    "*/.env",
    "*/.env.*",
    "*/.npmrc",
    "*/.netrc",
]


def access_policy_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "config" / "access_policy.json"


def access_audit_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "reports" / "access_audit.jsonl"


def access_consent_path_for(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / "config" / "access_consent.json"


def default_access_policy() -> dict[str, Any]:
    return {
        "access_policy_version": ACCESS_POLICY_VERSION,
        "allow_providers": DEFAULT_ALLOWED_PROVIDERS,
        "deny_providers": [],
        "deny_path_patterns": DEFAULT_DENY_PATH_PATTERNS,
        "require_consent_providers": [],
        "require_consent_path_patterns": [],
        "notes": [
            "deny_path_patterns use fnmatch against absolute POSIX paths and basenames",
            "require_consent_path_patterns use the same matching rules but only affect read_source",
            "deny rules win over allow rules",
            "raw path reads outside --out are rejected by MCP read_source even when not listed here",
        ],
        "audit_max_bytes": DEFAULT_ACCESS_AUDIT_MAX_BYTES,
        "audit_max_rotated_files": DEFAULT_ACCESS_AUDIT_MAX_ROTATED_FILES,
    }


def load_access_policy(out_root: Path) -> dict[str, Any]:
    policy = default_access_policy()
    path = access_policy_path_for(out_root)
    if path.exists():
        try:
            user_policy = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            user_policy = {}
        if isinstance(user_policy, dict):
            policy.update({key: value for key, value in user_policy.items() if value is not None})
    return policy


def write_default_access_policy(out_root: Path, *, overwrite: bool = False) -> dict[str, Any]:
    path = access_policy_path_for(out_root)
    if path.exists() and not overwrite:
        return {"policy_path": str(path), "written": False, "policy": load_access_policy(out_root)}
    ensure_dir(path.parent)
    policy = default_access_policy()
    write_text(path, json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"policy_path": str(path), "written": True, "policy": policy}


def update_access_policy(
    out_root: Path,
    *,
    allow_providers: list[str] | None = None,
    remove_allow_providers: list[str] | None = None,
    deny_providers: list[str] | None = None,
    remove_deny_providers: list[str] | None = None,
    deny_path_patterns: list[str] | None = None,
    remove_deny_path_patterns: list[str] | None = None,
    require_consent_providers: list[str] | None = None,
    remove_require_consent_providers: list[str] | None = None,
    require_consent_path_patterns: list[str] | None = None,
    remove_require_consent_path_patterns: list[str] | None = None,
    audit_max_bytes: int | None = None,
    audit_max_rotated_files: int | None = None,
) -> dict[str, Any]:
    path = access_policy_path_for(out_root)
    policy = load_access_policy(out_root)
    changes: list[str] = []

    changes.extend(_add_list_values(policy, "allow_providers", allow_providers))
    changes.extend(_remove_list_values(policy, "allow_providers", remove_allow_providers))
    changes.extend(_add_list_values(policy, "deny_providers", deny_providers))
    changes.extend(_remove_list_values(policy, "deny_providers", remove_deny_providers))
    changes.extend(_add_list_values(policy, "deny_path_patterns", deny_path_patterns))
    changes.extend(_remove_list_values(policy, "deny_path_patterns", remove_deny_path_patterns))
    changes.extend(_add_list_values(policy, "require_consent_providers", require_consent_providers))
    changes.extend(_remove_list_values(policy, "require_consent_providers", remove_require_consent_providers))
    changes.extend(_add_list_values(policy, "require_consent_path_patterns", require_consent_path_patterns))
    changes.extend(_remove_list_values(policy, "require_consent_path_patterns", remove_require_consent_path_patterns))
    if audit_max_bytes is not None:
        value = max(0, int(audit_max_bytes))
        if policy.get("audit_max_bytes") != value:
            policy["audit_max_bytes"] = value
            changes.append("set:audit_max_bytes")
    if audit_max_rotated_files is not None:
        value = max(0, int(audit_max_rotated_files))
        if policy.get("audit_max_rotated_files") != value:
            policy["audit_max_rotated_files"] = value
            changes.append("set:audit_max_rotated_files")

    ensure_dir(path.parent)
    write_text(path, json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "policy_path": str(path),
        "updated": bool(changes),
        "changes": changes,
        "policy": policy,
    }


def load_access_consent(out_root: Path) -> dict[str, Any]:
    path = access_consent_path_for(out_root)
    default = {"access_consent_version": ACCESS_CONSENT_VERSION, "grants": []}
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if not isinstance(data, dict):
        return default
    grants = data.get("grants")
    if not isinstance(grants, list):
        grants = []
    return {"access_consent_version": data.get("access_consent_version") or ACCESS_CONSENT_VERSION, "grants": grants}


def grant_access_consent(
    out_root: Path,
    *,
    identifier: str,
    record: dict[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    record = record or {}
    key = consent_key(identifier, record)
    path = access_consent_path_for(out_root)
    consent = load_access_consent(out_root)
    grants = [grant for grant in consent.get("grants") or [] if isinstance(grant, dict)]
    existing_keys = {str(grant.get("key") or "") for grant in grants}
    grant = {
        "access_consent_version": ACCESS_CONSENT_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "key": key,
        "identifier": identifier,
        "reason": reason,
        **record_summary(record),
    }
    written = False
    if key not in existing_keys:
        grants.append(grant)
        written = True
    ensure_dir(path.parent)
    write_text(
        path,
        json.dumps(
            {"access_consent_version": ACCESS_CONSENT_VERSION, "grants": grants},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    record_access_audit(
        out_root,
        action="grant_consent",
        decision="allowed",
        identifier=identifier,
        reason="consent_granted",
        record=record,
        details={"key": key, "written": written},
    )
    return {"consent_path": str(path), "written": written, "grant": grant, "grants_total": len(grants)}


def _add_list_values(policy: dict[str, Any], key: str, values: list[str] | None) -> list[str]:
    if not values:
        return []
    current = _policy_list(policy, key)
    changes = []
    for value in values:
        text = str(value).strip()
        if text and text not in current:
            current.append(text)
            changes.append(f"add:{key}:{text}")
    policy[key] = current
    return changes


def _remove_list_values(policy: dict[str, Any], key: str, values: list[str] | None) -> list[str]:
    if not values:
        return []
    current = _policy_list(policy, key)
    remove_values = {str(value).strip() for value in values if str(value).strip()}
    next_values = [value for value in current if value not in remove_values]
    policy[key] = next_values
    return [f"remove:{key}:{value}" for value in current if value in remove_values]


def _policy_list(policy: dict[str, Any], key: str) -> list[str]:
    return [str(value) for value in policy.get(key) or []]


def is_provider_allowed(policy: dict[str, Any], provider: str | None) -> bool:
    provider_value = str(provider or "")
    deny = {str(value) for value in policy.get("deny_providers") or []}
    allow = {str(value) for value in policy.get("allow_providers") or []}
    if provider_value and provider_value in deny:
        return False
    return not allow or not provider_value or provider_value in allow


def provider_decision(policy: dict[str, Any], provider: str | None) -> tuple[bool, str]:
    provider_value = str(provider or "")
    deny = {str(value) for value in policy.get("deny_providers") or []}
    allow = {str(value) for value in policy.get("allow_providers") or []}
    if provider_value and provider_value in deny:
        return False, f"provider_denied:{provider_value}"
    if allow and provider_value and provider_value not in allow:
        return False, f"provider_not_allowed:{provider_value}"
    return True, "allowed"


def is_path_allowed(policy: dict[str, Any], path_value: str | Path | None) -> bool:
    return path_decision(policy, path_value)[0]


def path_decision(policy: dict[str, Any], path_value: str | Path | None) -> tuple[bool, str]:
    if not path_value:
        return True, "allowed"
    path = Path(str(path_value)).expanduser()
    try:
        absolute = path.resolve()
    except OSError:
        absolute = path.absolute()
    absolute_text = absolute.as_posix()
    basename = absolute.name
    for pattern in policy.get("deny_path_patterns") or []:
        pattern_text = str(pattern)
        if fnmatch.fnmatch(absolute_text, pattern_text) or fnmatch.fnmatch(basename, pattern_text):
            return False, f"path_denied:{pattern_text}"
    return True, "allowed"


class ConsentRequiredError(PermissionError):
    def __init__(self, identifier: str, reason: str, record: dict[str, Any] | None = None) -> None:
        super().__init__(f"source requires consent before read_source: {identifier} ({reason})")
        self.identifier = identifier
        self.reason = reason
        self.record = record or {}


def record_access_audit(
    out_root: Path,
    *,
    action: str,
    decision: str,
    identifier: str = "",
    reason: str = "",
    record: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = record or {}
    event = {
        "access_audit_version": ACCESS_AUDIT_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "action": action,
        "decision": decision,
        "identifier": identifier,
        "reason": reason,
        "provider": source.get("provider") or source.get("parser") or source.get("policy") or "",
        "source_id": source.get("source_id") or source.get("doc_id") or "",
        "source_chunk_id": source.get("source_chunk_id") or source.get("chunk_id") or "",
        "path": str(source.get("path") or ""),
        "relative_path": str(source.get("relative_path") or ""),
        "details": details or {},
    }
    path = access_audit_path_for(out_root)
    _rotate_access_audit_if_needed(path, load_access_policy(out_root))
    append_jsonl(path, event)
    return event


def read_access_audit(out_root: Path, *, limit: int = 50) -> dict[str, Any]:
    path = access_audit_path_for(out_root)
    policy = load_access_policy(out_root)
    rotated_paths = _access_audit_rotated_paths(path, _audit_max_rotated_files(policy))
    events = []
    for rotated_path in reversed(rotated_paths):
        events.extend(_read_access_audit_file(rotated_path))
    events.extend(read_jsonl(path))
    normalized_limit = max(1, limit)
    return {
        "audit_path": str(path),
        "rotated_paths": [str(rotated_path) for rotated_path in rotated_paths if rotated_path.exists()],
        "events_total": len(events),
        "events": events[-normalized_limit:],
    }


def _audit_max_bytes(policy: dict[str, Any]) -> int:
    try:
        return int(policy.get("audit_max_bytes", DEFAULT_ACCESS_AUDIT_MAX_BYTES))
    except (TypeError, ValueError):
        return DEFAULT_ACCESS_AUDIT_MAX_BYTES


def _audit_max_rotated_files(policy: dict[str, Any]) -> int:
    try:
        return int(policy.get("audit_max_rotated_files", DEFAULT_ACCESS_AUDIT_MAX_ROTATED_FILES))
    except (TypeError, ValueError):
        return DEFAULT_ACCESS_AUDIT_MAX_ROTATED_FILES


def _access_audit_rotated_paths(path: Path, max_rotated_files: int) -> list[Path]:
    return [Path(f"{path}.{index}.gz") for index in range(1, max(0, max_rotated_files) + 1)]


def _rotate_access_audit_if_needed(path: Path, policy: dict[str, Any]) -> None:
    max_bytes = _audit_max_bytes(policy)
    max_rotated_files = _audit_max_rotated_files(policy)
    if max_bytes <= 0 or max_rotated_files <= 0 or not path.exists():
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < max_bytes:
        return

    ensure_dir(path.parent)
    oldest = Path(f"{path}.{max_rotated_files}.gz")
    if oldest.exists():
        oldest.unlink()
    for index in range(max_rotated_files - 1, 0, -1):
        current = Path(f"{path}.{index}.gz")
        if current.exists():
            current.rename(Path(f"{path}.{index + 1}.gz"))

    rotated = Path(f"{path}.1.gz")
    with path.open("rb") as source, gzip.open(rotated, "wb") as target:
        target.writelines(source)
    path.unlink()


def _read_access_audit_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    opener = gzip.open if path.suffix == ".gz" else Path.open
    records: list[dict[str, Any]] = []
    try:
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    return records


def record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": record.get("provider") or record.get("parser") or record.get("policy") or "",
        "source_id": record.get("source_id") or record.get("doc_id") or "",
        "source_chunk_id": record.get("source_chunk_id") or record.get("chunk_id") or "",
        "path": str(record.get("path") or ""),
        "relative_path": str(record.get("relative_path") or ""),
    }


def is_record_allowed(out_root: Path, record: dict[str, Any]) -> bool:
    policy = load_access_policy(out_root)
    return record_access_decision(policy, record)[0]


def record_access_decision(policy: dict[str, Any], record: dict[str, Any]) -> tuple[bool, str]:
    provider = record.get("provider") or record.get("parser") or record.get("policy")
    provider_allowed, provider_reason = provider_decision(policy, str(provider) if provider is not None else None)
    if not provider_allowed:
        return False, provider_reason
    return path_decision(policy, record.get("path"))


def consent_key(identifier: str, record: dict[str, Any]) -> str:
    for key in ("source_chunk_id", "chunk_id", "source_id", "doc_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    path_value = str(record.get("path") or "").strip()
    if path_value:
        return f"path:{_normalized_path_text(path_value)}"
    return f"identifier:{identifier.strip()}"


def consent_required_reason(policy: dict[str, Any], record: dict[str, Any]) -> str:
    provider = str(record.get("provider") or record.get("parser") or record.get("policy") or "")
    if provider and provider in {str(value) for value in policy.get("require_consent_providers") or []}:
        return f"consent_required:provider:{provider}"
    path_value = record.get("path")
    if path_value:
        absolute_text, basename = _path_match_text(path_value)
        for pattern in policy.get("require_consent_path_patterns") or []:
            pattern_text = str(pattern)
            if fnmatch.fnmatch(absolute_text, pattern_text) or fnmatch.fnmatch(basename, pattern_text):
                return f"consent_required:path:{pattern_text}"
    return ""


def has_access_consent(out_root: Path, identifier: str, record: dict[str, Any]) -> bool:
    key = consent_key(identifier, record)
    consent = load_access_consent(out_root)
    grants = [grant for grant in consent.get("grants") or [] if isinstance(grant, dict)]
    return any(str(grant.get("key") or "") == key for grant in grants)


def _path_match_text(path_value: str | Path) -> tuple[str, str]:
    path = Path(str(path_value)).expanduser()
    try:
        absolute = path.resolve()
    except OSError:
        absolute = path.absolute()
    absolute_text = absolute.as_posix()
    return absolute_text, absolute.name


def _normalized_path_text(path_value: str | Path) -> str:
    return _path_match_text(path_value)[0]


def filter_records_for_access(
    out_root: Path,
    records: list[dict[str, Any]],
    *,
    audit_action: str = "",
) -> list[dict[str, Any]]:
    policy = load_access_policy(out_root)
    allowed = []
    denied_samples = []
    denied_reasons: dict[str, int] = {}
    for record in records:
        is_allowed, reason = record_access_decision(policy, record)
        if is_allowed:
            allowed.append(record)
        else:
            denied_reasons[reason] = denied_reasons.get(reason, 0) + 1
            if len(denied_samples) < 10:
                denied_samples.append({**record_summary(record), "reason": reason})
    if audit_action:
        record_access_audit(
            out_root,
            action=audit_action,
            decision="filtered",
            reason="access_policy",
            details={
                "records": len(records),
                "allowed": len(allowed),
                "denied": len(records) - len(allowed),
                "denied_reasons": denied_reasons,
                "denied_samples": denied_samples,
            },
        )
    return allowed


def assert_path_allowed(out_root: Path, path: Path, identifier: str, *, action: str) -> None:
    policy = load_access_policy(out_root)
    allowed, reason = path_decision(policy, path)
    record = {"path": str(path)}
    record_access_audit(
        out_root,
        action=action,
        decision="allowed" if allowed else "denied",
        identifier=identifier,
        reason=reason,
        record=record,
    )
    if not allowed:
        raise PermissionError(f"generated artifact blocked by access policy: {identifier}")


def assert_record_allowed(out_root: Path, record: dict[str, Any], identifier: str, *, action: str = "read_source") -> None:
    policy = load_access_policy(out_root)
    allowed, reason = record_access_decision(policy, record)
    decision = "allowed" if allowed else "denied"
    if allowed:
        consent_reason = consent_required_reason(policy, record)
        if consent_reason and not has_access_consent(out_root, identifier, record):
            record_access_audit(
                out_root,
                action=action,
                decision="consent_required",
                identifier=identifier,
                reason=consent_reason,
                record=record,
                details={"consent_key": consent_key(identifier, record)},
            )
            raise ConsentRequiredError(identifier, consent_reason, record)
        if consent_reason:
            reason = f"consent_granted:{consent_reason}"
    record_access_audit(
        out_root,
        action=action,
        decision=decision,
        identifier=identifier,
        reason=reason,
        record=record,
    )
    if allowed:
        return
    raise PermissionError(f"source blocked by access policy: {identifier}")
