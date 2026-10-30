"""The request bodies of the write operations.

One place builds them, so a dry run shows exactly what a real run sends.
"""
from __future__ import annotations

from typing import Any


def transition_payload(transition_id: str, comment: str = "", fields: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"transition": {"id": transition_id}}
    if fields:
        payload["fields"] = dict(fields)
    if comment.strip():
        payload["update"] = {"comment": [{"add": {"body": comment.strip()}}]}
    return payload


def comment_payload(body: str, visibility: dict[str, str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"body": body.strip()}
    if visibility:
        payload["visibility"] = dict(visibility)
    return payload


def link_payload(type_name: str, outward_key: str, inward_key: str) -> dict[str, Any]:
    return {
        "type": {"name": type_name},
        "outwardIssue": {"key": outward_key},
        "inwardIssue": {"key": inward_key},
    }
