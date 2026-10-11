"""Reading the --policy-config file -- failing closed.

The gates default to allow (policy is an opt-in overlay), which makes a
policy file that *silently* loads as empty the worst possible failure: every
tool the author meant to deny or gate becomes allowed, and nothing says so.

Found 2026-10-11: Windows PowerShell 5.1's `Set-Content -Encoding utf8`
prepends a UTF-8 BOM. Read with the platform default encoding, the BOM became
part of the first key ("\\ufeffrules" / "ï»¿rules"), `data.get("rules")` was
None, and the proxy started with zero rules. A typo (`rule:`) did the same.
So: strip a BOM, and refuse -- at startup, loudly -- any file whose top level
is not exactly the keys verimcp understands.
"""
from pathlib import Path
from typing import Any

import yaml

KNOWN_KEYS = ("rules", "roles")


class PolicyFileError(ValueError):
    """The policy file cannot be trusted to mean what its author meant."""


def load_policy_file(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig"))  # -sig: drop a BOM if present
    if data is None:
        raise PolicyFileError(f"{path} is empty -- it defines no `rules` and no `roles`")
    if not isinstance(data, dict):
        raise PolicyFileError(f"{path} must be a mapping with `rules` and/or `roles`, got {type(data).__name__}")
    unknown = sorted(str(k) for k in data if k not in KNOWN_KEYS)
    if unknown:
        raise PolicyFileError(
            f"{path}: unknown top-level key(s) {unknown} -- expected {list(KNOWN_KEYS)}. "
            "Refusing to start: an unrecognized key would otherwise load as an empty policy, "
            "which allows every tool."
        )
    for rule in data.get("rules") or []:
        if not isinstance(rule, dict) or not isinstance(rule.get("tool"), str):
            raise PolicyFileError(f"{path}: every rule needs a `tool:` name, got {rule!r}")
    return data
