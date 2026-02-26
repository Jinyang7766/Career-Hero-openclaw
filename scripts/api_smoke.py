#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any
from urllib import error, request


def call(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any] | str]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw and raw.startswith(("{", "[")) else raw
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        parsed: dict[str, Any] | str
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return exc.code, parsed


def resolve_schema(spec: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    current: dict[str, Any] = schema or {}
    guard = 0
    while "$ref" in current and guard < 20:
        ref = current["$ref"]
        name = ref.rsplit("/", 1)[-1]
        current = spec.get("components", {}).get("schemas", {}).get(name, {})
        guard += 1
    return current


def build_min_value(spec: dict[str, Any], schema: dict[str, Any] | None, depth: int = 0) -> Any:
    if depth > 8:
        return "x"

    current = resolve_schema(spec, schema)

    if "default" in current:
        return current["default"]
    if "example" in current:
        return current["example"]
    if "enum" in current and current["enum"]:
        return current["enum"][0]

    for combiner in ("anyOf", "oneOf", "allOf"):
        options = current.get(combiner)
        if isinstance(options, list) and options:
            for option in options:
                resolved = resolve_schema(spec, option)
                if resolved.get("type") != "null":
                    return build_min_value(spec, resolved, depth + 1)

    schema_type = current.get("type")

    if schema_type == "string":
        if current.get("format") == "date-time":
            return "2026-01-01T00:00:00Z"
        min_length = max(1, int(current.get("minLength", 1)))
        return "x" * min_length

    if schema_type == "integer":
        minimum = int(current.get("minimum", 1))
        exclusive_min = current.get("exclusiveMinimum")
        if isinstance(exclusive_min, int):
            minimum = max(minimum, exclusive_min + 1)
        return max(1, minimum)

    if schema_type == "number":
        return max(1.0, float(current.get("minimum", 1.0)))

    if schema_type == "boolean":
        return False

    if schema_type == "array":
        item_schema = current.get("items", {})
        min_items = int(current.get("minItems", 0))
        if min_items > 0:
            return [build_min_value(spec, item_schema, depth + 1)]
        return []

    if schema_type == "object" or "properties" in current:
        result: dict[str, Any] = {}
        properties = current.get("properties", {})
        required = set(current.get("required", []))
        for field_name in required:
            result[field_name] = build_min_value(spec, properties.get(field_name, {}), depth + 1)
        return result

    return {}


def extract_json_schema(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if not isinstance(schema, dict):
        return None
    return resolve_schema(spec, schema)


def extract_session_id(payload: Any) -> str | int | None:
    if isinstance(payload, dict):
        preferred = ["sessionId", "session_id", "interviewSessionId", "interview_session_id", "id"]
        for key in preferred:
            value = payload.get(key)
            if isinstance(value, (str, int)):
                return value

        for key, value in payload.items():
            lowered = key.lower()
            if "session" in lowered and "id" in lowered and isinstance(value, (str, int)):
                return value

        for value in payload.values():
            found = extract_session_id(value)
            if found is not None:
                return found

    if isinstance(payload, list):
        for item in payload:
            found = extract_session_id(item)
            if found is not None:
                return found

    return None


def fill_session_path(path_template: str, session_id: str | int) -> str:
    return re.sub(r"\{[^}]*\}", str(session_id), path_template)


def find_interview_paths(spec: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    paths = spec.get("paths", {})
    create_path: str | None = None
    detail_path: str | None = None
    turn_path: str | None = None

    for path, methods in paths.items():
        lowered = path.lower()
        if "interview" not in lowered or "session" not in lowered:
            continue

        if "post" in methods and "{" not in path and create_path is None:
            create_path = path

        if "get" in methods and "{" in path and detail_path is None:
            detail_path = path

        if "post" in methods and "{" in path and any(tag in lowered for tag in ("message", "turn", "answer")):
            turn_path = path

    return create_path, detail_path, turn_path


def run_interview_smoke(base_url: str, spec: dict[str, Any], require_interview: bool) -> tuple[bool, str]:
    create_path, detail_path, turn_path = find_interview_paths(spec)
    if not create_path:
        if require_interview:
            return False, "interview APIs not found in /openapi.json"
        return True, "[SKIP] interview APIs are not exposed by current backend"

    create_op = spec["paths"][create_path]["post"]
    create_schema = extract_json_schema(spec, create_op)
    create_payload = build_min_value(spec, create_schema) if create_schema else {}
    if not isinstance(create_payload, dict):
        create_payload = {}

    status, created = call(base_url, "POST", create_path, create_payload)
    if status not in {200, 201} or not isinstance(created, dict):
        return False, f"create interview session failed: {status} {created}"

    session_id = extract_session_id(created)
    if session_id is None:
        return False, f"cannot locate session id in create response: {created}"

    if detail_path:
        status, detail_data = call(base_url, "GET", fill_session_path(detail_path, session_id))
        if status not in {200, 204}:
            return False, f"interview session detail failed: {status} {detail_data}"

    if turn_path:
        turn_op = spec["paths"][turn_path]["post"]
        turn_schema = extract_json_schema(spec, turn_op)
        turn_payload = build_min_value(spec, turn_schema) if turn_schema else {}
        if not isinstance(turn_payload, dict):
            turn_payload = {}

        status, turn_data = call(base_url, "POST", fill_session_path(turn_path, session_id), turn_payload)
        if status not in {200, 201, 204}:
            return False, f"interview turn failed: {status} {turn_data}"

    return True, "[PASS] interview chain"


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal API smoke test for Career Hero backend")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--require-interview", action="store_true", help="Fail when interview APIs are missing")
    args = parser.parse_args()

    status, health = call(args.base_url, "GET", "/health")
    if status != 200:
        print(f"[FAIL] /health => {status} {health}")
        return 1

    payload = {
        "resumeText": "5年 Python FastAPI SQL Docker 项目经验，负责日志与监控",
        "jdText": "岗位要求 Python FastAPI SQL Docker Redis",
    }
    status, analyze = call(args.base_url, "POST", "/api/analyze", payload)
    if status != 200 or not isinstance(analyze, dict) or "historyId" not in analyze:
        print(f"[FAIL] /api/analyze => {status} {analyze}")
        return 1

    history_id = analyze["historyId"]

    checks = [
        ("GET", "/api/history?limit=1"),
        ("GET", f"/api/history/{history_id}"),
        ("GET", f"/api/history/{history_id}/export?format=txt"),
        ("GET", "/api/metrics/snapshot"),
    ]

    for method, path in checks:
        status, data = call(args.base_url, method, path)
        if status != 200:
            print(f"[FAIL] {path} => {status} {data}")
            return 1

    status, openapi = call(args.base_url, "GET", "/openapi.json")
    if status != 200 or not isinstance(openapi, dict):
        print(f"[FAIL] /openapi.json => {status} {openapi}")
        return 1

    interview_ok, interview_message = run_interview_smoke(args.base_url, openapi, args.require_interview)
    print(interview_message)
    if not interview_ok:
        return 1

    print("[PASS] smoke checks completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
