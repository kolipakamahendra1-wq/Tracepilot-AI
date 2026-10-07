"""API-key RBAC. TRACEPILOT_API_KEYS="key:role:name,..." ; roles: viewer < investigator < approver < admin.

If no keys are configured, auth is disabled (local dev) and every caller is an admin named "dev".
"""
import hmac
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException

ROLES = ["viewer", "investigator", "approver", "admin"]


@dataclass
class Principal:
    name: str
    role: str


def load_keys(raw: str | None) -> dict[str, Principal]:
    keys = {}
    for item in filter(None, (raw or "").split(",")):
        key, role, name = (item.split(":") + ["", ""])[:3]
        if role not in ROLES:
            raise ValueError(f"bad role '{role}' in TRACEPILOT_API_KEYS")
        keys[key] = Principal(name or role, role)
    return keys


def require(role: str, keys: dict[str, Principal]):
    def dep(x_api_key: str | None = Header(default=None)) -> Principal:
        if not keys:
            return Principal("dev", "admin")
        match = next((p for k, p in keys.items() if x_api_key and hmac.compare_digest(k, x_api_key)), None)
        if match is None:
            raise HTTPException(401, "missing or invalid API key")
        if ROLES.index(match.role) < ROLES.index(role):
            raise HTTPException(403, f"role '{match.role}' cannot perform this action (needs '{role}')")
        return match

    return dep
