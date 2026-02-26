#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib import error, request


GLOBAL_HEADERS: dict[str, str] = {}


def call(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | list[Any] | str, dict[str, str]]:
    body = None
    req_headers = {"Accept": "application/json", **GLOBAL_HEADERS}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(f"{base_url.rstrip('/')}{path}", data=body, headers=req_headers, method=method)
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            parsed: dict[str, Any] | list[Any] | str
            if raw and raw.startswith(("{", "[")):
                parsed = json.loads(raw)
            else:
                parsed = raw
            return resp.status, parsed, dict(resp.headers.items())
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        parsed_error: dict[str, Any] | list[Any] | str
        try:
            parsed_error = json.loads(raw)
        except Exception:
            parsed_error = raw
        return exc.code, parsed_error, dict(exc.headers.items())
    except error.URLError as exc:
        return 0, str(exc), {}


def fail(step: str, detail: Any) -> int:
    print(f"[FAIL] {step}: {detail}")
    return 1


def get_header(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def bootstrap_auth_headers(base_url: str) -> dict[str, str]:
    session_id = "e2e-smoke-auth"
    status, payload, headers = call(
        base_url,
        "POST",
        "/api/auth/login",
        {"username": "demo", "password": "demo123456"},
        headers={"x-session-id": session_id, "x-request-id": "e2e-auth-login"},
    )

    if status != 200 or not isinstance(payload, dict):
        print(f"[WARN] auth bootstrap skipped: {status} {payload}")
        return {"x-session-id": session_id}

    token = payload.get("token")
    if not isinstance(token, str) or not token.strip():
        print("[WARN] auth bootstrap missing token; fallback to session-only headers")
        return {"x-session-id": session_id}

    normalized_session = payload.get("sessionId") if isinstance(payload.get("sessionId"), str) else None
    response_session = get_header(headers, "x-session-id")
    final_session_id = normalized_session or response_session or session_id

    return {
        "x-session-id": final_session_id,
        "authorization": f"Bearer {token.strip()}",
        "x-session-token": token.strip(),
    }


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


def resolve_schema(spec: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    current: dict[str, Any] = schema or {}
    guard = 0
    while "$ref" in current and guard < 20:
        ref = current["$ref"]
        name = ref.rsplit("/", 1)[-1]
        current = spec.get("components", {}).get("schemas", {}).get(name, {})
        guard += 1
    return current


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


def fill_session_path(path_template: str, session_id: str | int) -> str:
    return re.sub(r"\{[^}]*\}", str(session_id), path_template)


def find_interview_lifecycle_paths(
    spec: dict[str, Any],
) -> tuple[str | None, str | None, tuple[str, str] | None, tuple[str, str] | None, tuple[str, str] | None]:
    paths = spec.get("paths", {})

    create_path: str | None = None
    list_path: str | None = None
    detail: tuple[str, str] | None = None
    pause: tuple[str, str] | None = None
    resume: tuple[str, str] | None = None

    for path, methods in paths.items():
        lowered = path.lower()
        if "interview" not in lowered:
            continue

        if "post" in methods and "create" in lowered and create_path is None:
            create_path = path

        if "get" in methods and "{" not in path and any(tag in lowered for tag in ("sessions", "list")) and list_path is None:
            list_path = path

        if "{" in path:
            for method in ("get", "post", "patch", "put"):
                if method not in methods:
                    continue
                if detail is None and method == "get" and any(tag in lowered for tag in ("detail", "session")):
                    detail = (path, method)
                if pause is None and "pause" in lowered:
                    pause = (path, method)
                if resume is None and "resume" in lowered:
                    resume = (path, method)

    return create_path, list_path, detail, pause, resume


def run_optional_lifecycle(
    base_url: str,
    spec: dict[str, Any],
    created_session_id: str | int,
    request_headers: dict[str, str],
) -> tuple[bool, str]:
    create_path, list_path, detail, pause, resume = find_interview_lifecycle_paths(spec)
    missing = [
        name
        for name, value in (
            ("create", create_path),
            ("list", list_path),
            ("detail", detail),
            ("pause", pause),
            ("resume", resume),
        )
        if value is None
    ]
    if missing:
        return True, f"[SKIP] interview list/detail/pause/resume not fully exposed (missing: {', '.join(missing)})"

    status, _, _ = call(
        base_url,
        "GET",
        list_path,
        headers={**request_headers, "x-request-id": "e2e-interview-lifecycle-list"},
    )
    if status != 200:
        return False, f"list interview sessions failed: {status}"

    for title, op in (("detail", detail), ("pause", pause), ("resume", resume)):
        path, method = op
        operation = spec["paths"][path][method]
        schema = extract_json_schema(spec, operation)
        payload = build_min_value(spec, schema) if schema else None
        url = fill_session_path(path, created_session_id)
        headers = {**request_headers, "x-request-id": f"e2e-interview-lifecycle-{title}"}

        if isinstance(payload, dict):
            status, data, _ = call(base_url, method.upper(), url, payload, headers=headers)
        else:
            status, data, _ = call(base_url, method.upper(), url, headers=headers)

        if status not in {200, 201, 204}:
            return False, f"{title} interview session failed: {status} {data}"

    return True, "[PASS] interview list/detail/pause/resume"


def run_prd_v2_regression_checks(base_url: str) -> tuple[bool, list[str]]:
    messages: list[str] = []
    blockers: list[str] = []

    owner_headers = {"x-session-id": GLOBAL_HEADERS.get("x-session-id", "e2e-prd-v2-owner")}

    # 1) 选简历后默认走最新诊断步骤（latest version）
    status, created_resume, _ = call(
        base_url,
        "POST",
        "/api/resumes",
        {
            "title": "PRD v2 回归简历",
            "content": "版本1：Python FastAPI 基础经验",
        },
        headers={**owner_headers, "x-request-id": "e2e-prd-resume-create"},
    )
    if status != 200 or not isinstance(created_resume, dict):
        return False, [f"[BLOCKER] create resume failed: {status} {created_resume}"]

    created_item = created_resume.get("item") if isinstance(created_resume, dict) else None
    if not isinstance(created_item, dict) or not isinstance(created_item.get("id"), int):
        return False, [f"[BLOCKER] invalid resume create payload: {created_resume}"]

    resume_id = int(created_item["id"])

    status, updated_resume, _ = call(
        base_url,
        "PUT",
        f"/api/resumes/{resume_id}",
        {
            "content": "版本2：Python FastAPI Redis SQL 监控优化项目经验",
            "createNewVersion": True,
        },
        headers={**owner_headers, "x-request-id": "e2e-prd-resume-update"},
    )
    if status != 200 or not isinstance(updated_resume, dict):
        return False, [f"[BLOCKER] update resume failed: {status} {updated_resume}"]

    updated_item = updated_resume.get("item") if isinstance(updated_resume, dict) else None
    latest_version_no = (updated_item or {}).get("latestVersionNo") if isinstance(updated_item, dict) else None
    if latest_version_no != 2:
        blockers.append(f"[BLOCKER] resume latestVersionNo expected 2, got {latest_version_no}")

    analyze_payload_base = {
        "resumeId": resume_id,
        "jdText": "岗位要求：Python FastAPI Redis SQL 监控",
    }

    status, latest_default, _ = call(
        base_url,
        "POST",
        "/api/analyze",
        analyze_payload_base,
        headers={**owner_headers, "x-request-id": "e2e-prd-analyze-latest-default"},
    )
    if status != 200 or not isinstance(latest_default, dict):
        return False, [f"[BLOCKER] analyze latest(default) failed: {status} {latest_default}"]

    status, latest_explicit, _ = call(
        base_url,
        "POST",
        "/api/analyze",
        {**analyze_payload_base, "versionNo": 2},
        headers={**owner_headers, "x-request-id": "e2e-prd-analyze-latest-explicit"},
    )
    if status != 200 or not isinstance(latest_explicit, dict):
        return False, [f"[BLOCKER] analyze latest(explicit) failed: {status} {latest_explicit}"]

    status, old_version, _ = call(
        base_url,
        "POST",
        "/api/analyze",
        {**analyze_payload_base, "versionNo": 1},
        headers={**owner_headers, "x-request-id": "e2e-prd-analyze-old-version"},
    )
    if status != 200 or not isinstance(old_version, dict):
        return False, [f"[BLOCKER] analyze old(version1) failed: {status} {old_version}"]

    history_ids: list[int] = []
    for payload in (latest_default, latest_explicit, old_version):
        hid = payload.get("historyId") if isinstance(payload, dict) else None
        if isinstance(hid, int):
            history_ids.append(hid)

    if len(history_ids) != 3:
        blockers.append(f"[BLOCKER] cannot locate complete history IDs for latest-version check: {history_ids}")
    else:
        latest_default_id, latest_explicit_id, old_version_id = history_ids
        status, latest_default_detail, _ = call(
            base_url,
            "GET",
            f"/api/history/{latest_default_id}",
            headers={**owner_headers, "x-request-id": "e2e-prd-history-latest-default"},
        )
        status2, latest_explicit_detail, _ = call(
            base_url,
            "GET",
            f"/api/history/{latest_explicit_id}",
            headers={**owner_headers, "x-request-id": "e2e-prd-history-latest-explicit"},
        )
        status3, old_version_detail, _ = call(
            base_url,
            "GET",
            f"/api/history/{old_version_id}",
            headers={**owner_headers, "x-request-id": "e2e-prd-history-old"},
        )

        if status != 200 or status2 != 200 or status3 != 200:
            blockers.append(
                "[BLOCKER] failed to fetch history details for latest-version assertion: "
                f"{status}/{status2}/{status3}"
            )
        else:
            latest_default_item = latest_default_detail.get("item") if isinstance(latest_default_detail, dict) else None
            latest_explicit_item = latest_explicit_detail.get("item") if isinstance(latest_explicit_detail, dict) else None
            old_version_item = old_version_detail.get("item") if isinstance(old_version_detail, dict) else None

            latest_default_hash = (latest_default_item or {}).get("resumeTextHashOrExcerpt") if isinstance(latest_default_item, dict) else None
            latest_explicit_hash = (latest_explicit_item or {}).get("resumeTextHashOrExcerpt") if isinstance(latest_explicit_item, dict) else None
            old_version_hash = (old_version_item or {}).get("resumeTextHashOrExcerpt") if isinstance(old_version_item, dict) else None

            if not latest_default_hash or latest_default_hash != latest_explicit_hash:
                blockers.append("[BLOCKER] selecting resume without versionNo did not match latest version result")
            if latest_default_hash == old_version_hash:
                blockers.append("[BLOCKER] latest and old resume versions produced identical resume hash unexpectedly")

    messages.append("[PASS] resume latest-version default selection")

    # 2) 多简历进度不串扰 + 6) 返回列表再进入锁定状态仍正确
    status, interview_a, _ = call(
        base_url,
        "POST",
        "/api/interview/session/create",
        {
            "jdText": "岗位A：后端开发",
            "resumeText": "简历A：后端开发经验",
            "questionCount": 3,
        },
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-create-a"},
    )
    status2, interview_b, _ = call(
        base_url,
        "POST",
        "/api/interview/session/create",
        {
            "jdText": "岗位B：数据分析",
            "resumeText": "简历B：数据分析经验",
            "questionCount": 3,
        },
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-create-b"},
    )

    if status != 200 or status2 != 200 or not isinstance(interview_a, dict) or not isinstance(interview_b, dict):
        return False, [f"[BLOCKER] create interview sessions failed: {status}/{status2}"]

    session_a = ((interview_a.get("session") or {}) if isinstance(interview_a, dict) else {}).get("id")
    session_b = ((interview_b.get("session") or {}) if isinstance(interview_b, dict) else {}).get("id")

    if not isinstance(session_a, int) or not isinstance(session_b, int):
        return False, [f"[BLOCKER] invalid interview ids: {interview_a} / {interview_b}"]

    status, answer_a, _ = call(
        base_url,
        "POST",
        f"/api/interview/session/{session_a}/answer",
        {"answerText": "先做瓶颈定位再优化", "questionIndex": 0},
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-answer-a"},
    )
    if status != 200:
        blockers.append(f"[BLOCKER] answer on session A failed: {status} {answer_a}")

    status, detail_a, _ = call(
        base_url,
        "GET",
        f"/api/interview/sessions/{session_a}",
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-detail-a"},
    )
    status2, detail_b, _ = call(
        base_url,
        "GET",
        f"/api/interview/sessions/{session_b}",
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-detail-b"},
    )

    if status != 200 or status2 != 200:
        blockers.append(f"[BLOCKER] cannot fetch interview details for isolation check: {status}/{status2}")
    else:
        answered_a = ((detail_a.get("session") or {}) if isinstance(detail_a, dict) else {}).get("answeredCount")
        answered_b = ((detail_b.get("session") or {}) if isinstance(detail_b, dict) else {}).get("answeredCount")
        if answered_a != 1 or answered_b != 0:
            blockers.append(
                f"[BLOCKER] progress crosstalk detected: answeredCount A/B expected 1/0, got {answered_a}/{answered_b}"
            )

    status, paused, _ = call(
        base_url,
        "POST",
        f"/api/interview/session/{session_a}/pause",
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-pause-a"},
    )
    if status != 200:
        blockers.append(f"[BLOCKER] pause session A failed: {status} {paused}")

    status, blocked_answer, _ = call(
        base_url,
        "POST",
        f"/api/interview/session/{session_a}/answer",
        {"answerText": "paused state should reject", "questionIndex": 1},
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-paused-answer"},
    )
    if status != 400:
        blockers.append(f"[BLOCKER] paused interview should lock answer input, got {status} {blocked_answer}")

    status, listed, _ = call(
        base_url,
        "GET",
        "/api/interview/sessions?limit=20",
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-list"},
    )
    if status != 200 or not isinstance(listed, dict):
        blockers.append(f"[BLOCKER] interview list failed for re-entry check: {status} {listed}")
    else:
        listed_items = listed.get("items") if isinstance(listed, dict) else None
        target = None
        if isinstance(listed_items, list):
            target = next((item for item in listed_items if isinstance(item, dict) and item.get("id") == session_a), None)
        if not isinstance(target, dict) or target.get("status") != "paused":
            blockers.append(f"[BLOCKER] paused status lost after returning to list: {target}")

    status, detail_after_pause, _ = call(
        base_url,
        "GET",
        f"/api/interview/sessions/{session_a}",
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-detail-after-pause"},
    )
    if status != 200 or not isinstance(detail_after_pause, dict):
        blockers.append(f"[BLOCKER] interview detail after list re-entry failed: {status} {detail_after_pause}")
    else:
        detail_status = ((detail_after_pause.get("session") or {}) if isinstance(detail_after_pause, dict) else {}).get("status")
        if detail_status != "paused":
            blockers.append(f"[BLOCKER] paused lock state lost after re-entry: {detail_status}")

    messages.append("[PASS] interview progress isolation + lock-state re-entry")

    # 4) 面试启动可进入会话（降级路径：JD-only）
    status, jd_only_start, _ = call(
        base_url,
        "POST",
        "/api/interview/session/create",
        {
            "jdText": "岗位要求：后端开发，独立交付",
            "questionCount": 3,
        },
        headers={**owner_headers, "x-request-id": "e2e-prd-interview-jd-only"},
    )
    if status not in {200, 201}:
        blockers.append(f"[BLOCKER] interview start (JD-only degrade path) failed: {status} {jd_only_start}")

    messages.append("[PASS] interview start entry (JD-only degraded path)")

    # 3) 报告页返回单击生效（静态回归守卫：允许旧实现(query保留)与新实现(route meta 回退)）
    app_shell_path = Path(__file__).resolve().parents[1] / "frontend" / "src" / "app" / "components" / "AppShell.tsx"
    try:
        app_shell_code = app_shell_path.read_text(encoding="utf-8")

        legacy_required_snippets = [
            "matched?.prefix === \"/interview/summary\"",
            "searchParams.get(\"sessionId\")",
            "searchParams.get(\"sessionKey\")",
            "sectionHref: next.toString() ? `/interview?${next.toString()}` : \"/interview\"",
        ]
        legacy_ok = all(snippet in app_shell_code for snippet in legacy_required_snippets)

        route_meta_ok = bool(
            re.search(
                r'\{[^{}]*prefix:\s*"/interview/summary"[^{}]*sectionHref:\s*"/interview"[^{}]*\}',
                app_shell_code,
                flags=re.DOTALL,
            )
        )
        route_back_link_ok = "href={route.sectionHref}" in app_shell_code and "resolveRoute(" in app_shell_code

        if not (legacy_ok or (route_meta_ok and route_back_link_ok)):
            blockers.append(
                "[BLOCKER] report-page back-route static guard not found (need legacy query-preserve guard "
                "or route meta summary->/interview fallback)."
            )
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"[BLOCKER] cannot read AppShell.tsx for report-back guard: {exc}")

    messages.append("[PASS] report-page back-route static guard")

    # 7) 上次修改展示 contentUpdatedAt 优先
    status, resume_list, _ = call(
        base_url,
        "GET",
        "/api/resumes?limit=20",
        headers={**owner_headers, "x-request-id": "e2e-prd-resume-list"},
    )
    if status != 200 or not isinstance(resume_list, dict):
        blockers.append(f"[BLOCKER] resume list failed for last-modified check: {status} {resume_list}")
    else:
        items = resume_list.get("items") if isinstance(resume_list, dict) else None
        first_item = items[0] if isinstance(items, list) and items else None
        if not isinstance(first_item, dict):
            blockers.append("[BLOCKER] resume list is empty; cannot verify last-modified field")
        else:
            # PRD要求 contentUpdatedAt 优先，当前接口若未暴露则直接阻塞。
            if "contentUpdatedAt" not in first_item:
                blockers.append(
                    "[BLOCKER] /api/resumes item missing contentUpdatedAt; cannot enforce PRD last-modified priority"
                )

    messages.append("[PASS] resume last-modified contract check executed")

    if blockers:
        return False, blockers

    return True, messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Extended E2E smoke test for Career Hero backend")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    args = parser.parse_args()

    status, health, _ = call(args.base_url, "GET", "/health")
    if status != 200:
        return fail("/health", f"{status} {health}")

    global GLOBAL_HEADERS
    GLOBAL_HEADERS = bootstrap_auth_headers(args.base_url)

    knowledge_items = [
        {
            "title": "Redis 缓存命中优化",
            "content": "在 FastAPI 服务引入 Redis 缓存，显著降低 SQL 压力并提升响应速度。",
            "tags": ["redis", "fastapi", "sql"],
            "source": "smoke",
        },
        {
            "title": "监控与告警实践",
            "content": "通过 Prometheus + Grafana 建立 Python 服务可观测体系与告警策略。",
            "tags": ["monitoring", "python", "docker"],
            "source": "smoke",
        },
    ]
    for idx, item in enumerate(knowledge_items, start=1):
        status, data, _ = call(
            args.base_url,
            "POST",
            "/api/rag/knowledge",
            item,
            headers={"x-request-id": f"e2e-knowledge-{idx}"},
        )
        if status != 200:
            return fail(f"/api/rag/knowledge #{idx}", f"{status} {data}")

    analyze_payload = {
        "resumeText": "5年 Python FastAPI SQL Docker 项目经验，负责日志与监控",
        "jdText": "岗位要求 Python FastAPI SQL Docker Redis 监控",
    }

    owner_session_id = GLOBAL_HEADERS.get("x-session-id", "e2e-smoke-session")
    status, analyze_off, analyze_off_headers = call(
        args.base_url,
        "POST",
        "/api/analyze",
        {**analyze_payload, "ragEnabled": False},
        headers={"x-request-id": "e2e-analyze-off", "x-session-id": owner_session_id},
    )
    if status != 200 or not isinstance(analyze_off, dict):
        return fail("/api/analyze ragEnabled=false", f"{status} {analyze_off}")

    normalized_owner_session = get_header(analyze_off_headers, "x-session-id") or owner_session_id
    history_headers = {"x-session-id": normalized_owner_session}

    history_id = analyze_off.get("historyId")
    if not isinstance(history_id, int):
        return fail("history id", f"invalid historyId: {analyze_off}")

    if analyze_off.get("ragEnabled") is not False or analyze_off.get("ragHits") != []:
        return fail("rag disabled response", analyze_off)

    status, analyze_on, _ = call(
        args.base_url,
        "POST",
        "/api/analyze",
        {**analyze_payload, "ragEnabled": True, "ragTopK": 3},
        headers={"x-request-id": "e2e-analyze-on", "x-session-id": owner_session_id},
    )
    if status != 200 or not isinstance(analyze_on, dict):
        return fail("/api/analyze ragEnabled=true", f"{status} {analyze_on}")

    rag_hits = analyze_on.get("ragHits")
    if analyze_on.get("ragEnabled") is not True or not isinstance(rag_hits, list) or len(rag_hits) > 3:
        return fail("rag enabled response", analyze_on)

    status, history_list, _ = call(
        args.base_url,
        "GET",
        "/api/history?limit=5&requestId=e2e-analyze-off",
        headers={**history_headers, "x-request-id": "e2e-history-list"},
    )
    if status != 200 or not isinstance(history_list, dict):
        return fail("/api/history list", f"{status} {history_list}")

    history_items = history_list.get("items") if isinstance(history_list, dict) else None
    if not isinstance(history_items, list) or not any(isinstance(item, dict) and item.get("id") == history_id for item in history_items):
        return fail("/api/history list content", history_list)

    status, history_detail, _ = call(
        args.base_url,
        "GET",
        f"/api/history/{history_id}",
        headers={**history_headers, "x-request-id": "e2e-history-detail"},
    )
    if status != 200 or not isinstance(history_detail, dict):
        return fail("/api/history/{id}", f"{status} {history_detail}")

    detail_item = history_detail.get("item") if isinstance(history_detail, dict) else None
    if not isinstance(detail_item, dict) or detail_item.get("id") != history_id:
        return fail("/api/history/{id} content", history_detail)

    status, _, export_headers = call(
        args.base_url,
        "GET",
        f"/api/history/{history_id}/export?format=txt",
        headers={**history_headers, "x-request-id": "e2e-history-export"},
    )
    if status != 200:
        return fail("/api/history export", status)
    if "content-disposition" not in {k.lower() for k in export_headers}:
        return fail("/api/history export header", export_headers)

    interview_session_headers = {"x-session-id": owner_session_id}
    interview_create_payload = {
        "jdText": "岗位要求 Python FastAPI SQL Docker Redis",
        "resumeText": "3年后端开发经验，熟悉 Python FastAPI SQL Docker",
        "questionCount": 3,
    }
    status, interview_created, _ = call(
        args.base_url,
        "POST",
        "/api/interview/session/create",
        interview_create_payload,
        headers={**interview_session_headers, "x-request-id": "e2e-interview-create"},
    )
    if status not in {200, 201} or not isinstance(interview_created, dict):
        return fail("/api/interview/session/create", f"{status} {interview_created}")

    session_id = extract_session_id(interview_created)
    if session_id is None:
        return fail("interview session id", interview_created)

    status, openapi, _ = call(args.base_url, "GET", "/openapi.json")
    if status != 200 or not isinstance(openapi, dict):
        return fail("/openapi.json", f"{status} {openapi}")

    lifecycle_ok, lifecycle_message = run_optional_lifecycle(
        args.base_url,
        openapi,
        session_id,
        request_headers=interview_session_headers,
    )
    print(lifecycle_message)
    if not lifecycle_ok:
        return 1

    status, interview_next, _ = call(
        args.base_url,
        "POST",
        f"/api/interview/session/{session_id}/next",
        headers={**interview_session_headers, "x-request-id": "e2e-interview-next"},
    )
    if status not in {200, 201} or not isinstance(interview_next, dict):
        return fail("/api/interview/session/{id}/next", f"{status} {interview_next}")

    next_question = interview_next.get("nextQuestion") if isinstance(interview_next, dict) else None
    question_index = 0
    if isinstance(next_question, dict) and isinstance(next_question.get("index"), int):
        question_index = int(next_question["index"])

    answer_payload = {
        "answerText": "我会先做瓶颈定位，再用 Redis 缓存与 SQL 索引优化提升性能。",
        "questionIndex": question_index,
    }
    status, interview_answer, _ = call(
        args.base_url,
        "POST",
        f"/api/interview/session/{session_id}/answer",
        answer_payload,
        headers={**interview_session_headers, "x-request-id": "e2e-interview-answer"},
    )
    if status not in {200, 201}:
        return fail("/api/interview/session/{id}/answer", f"{status} {interview_answer}")

    status, interview_finish, _ = call(
        args.base_url,
        "POST",
        f"/api/interview/session/{session_id}/finish",
        headers={**interview_session_headers, "x-request-id": "e2e-interview-finish"},
    )
    if status not in {200, 201}:
        return fail("/api/interview/session/{id}/finish", f"{status} {interview_finish}")

    prd_ok, prd_messages = run_prd_v2_regression_checks(args.base_url)
    for msg in prd_messages:
        print(msg)
    if not prd_ok:
        return 1

    print("[PASS] e2e smoke checks completed (analyze + rag + interview + history + prd-v2-regression)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
