from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "career_hero_prd_followup_test.sqlite3"
    monkeypatch.setenv("CAREER_HERO_DB_PATH", str(db_path))
    monkeypatch.setenv("CAREER_HERO_AI_PROVIDER", "rule")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("CAREER_HERO_AUTH_MODE", "local")
    monkeypatch.setenv("CAREER_HERO_REQUIRE_LOGIN_FOR_PROTECTED", "false")
    monkeypatch.setattr(
        main_module,
        "RATE_LIMITER",
        main_module.SessionRateLimiter(
            limit=20,
            window_seconds=60,
            duplicate_limit=3,
            duplicate_window_seconds=15,
        ),
    )
    monkeypatch.setattr(main_module, "METRICS", main_module.MetricsTracker())
    monkeypatch.setattr(main_module, "DEFAULT_HISTORY_RETENTION", 500)


def assert_error_shape(data: dict[str, Any]) -> None:
    assert isinstance(data.get("code"), str)
    assert data["code"]
    assert isinstance(data.get("message"), str)
    assert data["message"]
    assert isinstance(data.get("requestId"), str)
    assert data["requestId"]


def make_payload(i: int = 1) -> dict[str, str]:
    return {
        "resumeText": f"候选人{i}：5年Python FastAPI SQL Docker经验，做过日志与监控。",
        "jdText": "岗位要求：Python FastAPI SQL Docker Redis 监控",
    }


def _openapi() -> dict[str, Any]:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


def _resolve_schema(spec: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    current: dict[str, Any] = schema or {}
    guard = 0
    while "$ref" in current and guard < 20:
        ref = current["$ref"]
        name = ref.rsplit("/", 1)[-1]
        current = spec.get("components", {}).get("schemas", {}).get(name, {})
        guard += 1
    return current


def _extract_json_schema(spec: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any] | None:
    body = operation.get("requestBody", {})
    content = body.get("content", {})
    schema = content.get("application/json", {}).get("schema")
    if not isinstance(schema, dict):
        return None
    return _resolve_schema(spec, schema)


def _build_min_value(spec: dict[str, Any], schema: dict[str, Any] | None, depth: int = 0) -> Any:
    if depth > 8:
        return "x"

    current = _resolve_schema(spec, schema)

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
                resolved = _resolve_schema(spec, option)
                if resolved.get("type") != "null":
                    return _build_min_value(spec, resolved, depth + 1)

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
        minimum = float(current.get("minimum", 1.0))
        return max(1.0, minimum)

    if schema_type == "boolean":
        return False

    if schema_type == "array":
        item_schema = current.get("items", {})
        min_items = int(current.get("minItems", 0))
        if min_items > 0:
            return [_build_min_value(spec, item_schema, depth + 1)]
        return []

    if schema_type == "object" or "properties" in current:
        result: dict[str, Any] = {}
        properties = current.get("properties", {})
        required = set(current.get("required", []))
        for field_name in required:
            field_schema = properties.get(field_name, {})
            result[field_name] = _build_min_value(spec, field_schema, depth + 1)
        return result

    return {}


def _find_analyze_request_rag_field(spec: dict[str, Any]) -> str | None:
    operation = spec.get("paths", {}).get("/api/analyze", {}).get("post", {})
    schema = _extract_json_schema(spec, operation)
    if not schema:
        return None

    properties = schema.get("properties", {})
    for key in properties:
        if "rag" in key.lower():
            return key
    return None


def _find_analyze_response_pip_field(spec: dict[str, Any]) -> str | None:
    operation = spec.get("paths", {}).get("/api/analyze", {}).get("post", {})
    responses = operation.get("responses", {})
    schema = (
        responses.get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if not isinstance(schema, dict):
        return None

    resolved = _resolve_schema(spec, schema)
    for key in resolved.get("properties", {}):
        lowered = key.lower()
        if "pipadvice" in lowered or "pip_advice" in lowered:
            return key
    return None


def _extract_session_id(payload: Any) -> str | int | None:
    if isinstance(payload, dict):
        preferred_keys = [
            "sessionId",
            "session_id",
            "id",
            "interviewSessionId",
            "interview_session_id",
        ]
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, (str, int)):
                return value
        for key, value in payload.items():
            lowered = key.lower()
            if "session" in lowered and "id" in lowered and isinstance(value, (str, int)):
                return value
        for value in payload.values():
            found = _extract_session_id(value)
            if found is not None:
                return found

    if isinstance(payload, list):
        for item in payload:
            found = _extract_session_id(item)
            if found is not None:
                return found

    return None


def _fill_session_path(path_template: str, session_id: str | int) -> str:
    return re.sub(r"\{[^}]*\}", str(session_id), path_template)


def _find_interview_paths(spec: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
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


def test_analyze_response_contains_pip_advice_contract_when_supported() -> None:
    spec = _openapi()
    pip_field = _find_analyze_response_pip_field(spec)
    if not pip_field:
        pytest.skip("pipAdvice capability is not exposed in current API schema")

    resp = client.post(
        "/api/analyze",
        json=make_payload(),
        headers={"x-request-id": "req-pip-advice-contract", "x-session-id": "session-pip-advice"},
    )
    assert resp.status_code == 200

    data = resp.json()
    assert pip_field in data
    pip_advice = data[pip_field]
    assert isinstance(pip_advice, list)
    if pip_advice:
        assert isinstance(pip_advice[0], (str, dict))


def test_analyze_rag_switch_contract_when_supported() -> None:
    spec = _openapi()
    rag_field = _find_analyze_request_rag_field(spec)
    if not rag_field:
        pytest.skip("RAG toggle is not exposed in /api/analyze request schema")

    for enabled in (False, True):
        payload = make_payload(100 if enabled else 99)
        payload[rag_field] = enabled

        resp = client.post(
            "/api/analyze",
            json=payload,
            headers={"x-request-id": f"req-rag-{enabled}", "x-session-id": "session-rag-toggle"},
        )

        assert resp.status_code in {200, 400, 422}
        if resp.status_code == 200:
            assert isinstance(resp.json().get("historyId"), int)
        else:
            assert_error_shape(resp.json())


def test_interview_session_api_minimal_chain_when_supported() -> None:
    spec = _openapi()
    create_path, detail_path, turn_path = _find_interview_paths(spec)
    if not create_path:
        pytest.skip("Interview session API is not exposed in current OpenAPI paths")

    create_operation = spec["paths"][create_path]["post"]
    create_payload_schema = _extract_json_schema(spec, create_operation)
    create_payload = _build_min_value(spec, create_payload_schema) if create_payload_schema else {}
    if not isinstance(create_payload, dict):
        create_payload = {}

    create_resp = client.post(create_path, json=create_payload)
    assert create_resp.status_code in {200, 201}, create_resp.text

    created = create_resp.json() if create_resp.headers.get("content-type", "").startswith("application/json") else {}
    session_id = _extract_session_id(created)
    assert session_id is not None, f"cannot locate session id in create response: {created}"

    if detail_path:
        detail_resp = client.get(_fill_session_path(detail_path, session_id))
        assert detail_resp.status_code in {200, 204}, detail_resp.text

    if turn_path:
        turn_operation = spec["paths"][turn_path]["post"]
        turn_payload_schema = _extract_json_schema(spec, turn_operation)
        turn_payload = _build_min_value(spec, turn_payload_schema) if turn_payload_schema else {}
        if not isinstance(turn_payload, dict):
            turn_payload = {}

        turn_resp = client.post(_fill_session_path(turn_path, session_id), json=turn_payload)
        assert turn_resp.status_code in {200, 201, 204}, turn_resp.text


def _find_interview_lifecycle_paths(
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


def _call_operation_with_min_payload(
    *,
    spec: dict[str, Any],
    method: str,
    path: str,
    session_id: str | int,
) -> Any:
    operation = spec["paths"][path][method]
    payload_schema = _extract_json_schema(spec, operation)
    payload = _build_min_value(spec, payload_schema) if payload_schema else None

    url = _fill_session_path(path, session_id)
    if isinstance(payload, dict):
        return client.request(method.upper(), url, json=payload)
    return client.request(method.upper(), url)


def test_interview_list_detail_pause_resume_when_supported() -> None:
    spec = _openapi()
    create_path, list_path, detail, pause, resume = _find_interview_lifecycle_paths(spec)

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
        pytest.skip(f"interview list/detail/pause/resume APIs not fully exposed: missing {', '.join(missing)}")

    create_operation = spec["paths"][create_path]["post"]
    create_schema = _extract_json_schema(spec, create_operation)
    create_payload = _build_min_value(spec, create_schema) if create_schema else {}
    if not isinstance(create_payload, dict):
        create_payload = {}

    create_resp = client.post(create_path, json=create_payload)
    assert create_resp.status_code in {200, 201}, create_resp.text

    created = create_resp.json() if create_resp.headers.get("content-type", "").startswith("application/json") else {}
    session_id = _extract_session_id(created)
    assert session_id is not None

    list_resp = client.get(list_path)
    assert list_resp.status_code == 200, list_resp.text

    detail_path, detail_method = detail
    detail_resp = _call_operation_with_min_payload(
        spec=spec,
        method=detail_method,
        path=detail_path,
        session_id=session_id,
    )
    assert detail_resp.status_code in {200, 204}, detail_resp.text

    pause_path, pause_method = pause
    pause_resp = _call_operation_with_min_payload(
        spec=spec,
        method=pause_method,
        path=pause_path,
        session_id=session_id,
    )
    assert pause_resp.status_code in {200, 201, 204}, pause_resp.text

    resume_path, resume_method = resume
    resume_resp = _call_operation_with_min_payload(
        spec=spec,
        method=resume_method,
        path=resume_path,
        session_id=session_id,
    )
    assert resume_resp.status_code in {200, 201, 204}, resume_resp.text


def _extract_access_token(payload: Any) -> str | None:
    if isinstance(payload, dict):
        preferred_keys = ["accessToken", "access_token", "token", "jwt"]
        for key in preferred_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        for key, value in payload.items():
            lowered = key.lower()
            if "token" in lowered and isinstance(value, str) and value.strip():
                return value.strip()

        for value in payload.values():
            found = _extract_access_token(value)
            if found:
                return found

    if isinstance(payload, list):
        for item in payload:
            found = _extract_access_token(item)
            if found:
                return found

    return None


def _request_with_optional_json(
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
):
    if isinstance(payload, dict):
        return client.request(method.upper(), path, json=payload, headers=headers)
    return client.request(method.upper(), path, headers=headers)


def _find_knowledge_update_delete_path(spec: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    paths = spec.get("paths", {})
    for path, methods in paths.items():
        lowered = path.lower()
        if not lowered.startswith("/api/rag/knowledge"):
            continue
        if "{" not in path:
            continue

        update_method = "put" if "put" in methods else "patch" if "patch" in methods else None
        delete_method = "delete" if "delete" in methods else None
        if update_method and delete_method:
            return path, update_method, delete_method

    return None, None, None


def test_wave5_auth_login_chain_contract_and_flow(monkeypatch) -> None:
    monkeypatch.setenv("CAREER_HERO_DEFAULT_USERNAME", "demo")
    monkeypatch.setenv("CAREER_HERO_DEFAULT_PASSWORD", "demo123456")
    main_module.ensure_default_local_account()

    spec = _openapi()
    required = [
        ("post", "/api/auth/login"),
        ("get", "/api/auth/me"),
        ("post", "/api/auth/logout"),
    ]

    missing = [
        f"{method.upper()} {path}"
        for method, path in required
        if method not in spec.get("paths", {}).get(path, {})
    ]
    assert not missing, (
        "Wave5 blocker: auth login chain APIs missing from OpenAPI: "
        + ", ".join(missing)
    )

    login_operation = spec["paths"]["/api/auth/login"]["post"]
    login_schema = _extract_json_schema(spec, login_operation)
    login_payload = _build_min_value(spec, login_schema) if login_schema else {}
    if not isinstance(login_payload, dict):
        login_payload = {}

    login_payload["username"] = "demo"
    login_payload["password"] = "demo123456"

    auth_headers = {"x-session-id": "user:1", "x-request-id": "wave5-auth-login"}
    login_resp = client.post("/api/auth/login", json=login_payload, headers=auth_headers)
    assert login_resp.status_code == 200, login_resp.text

    login_data = login_resp.json()
    token = _extract_access_token(login_data)
    assert token, f"login response missing token: {login_data}"

    me_headers = {
        "x-session-id": auth_headers["x-session-id"],
        "authorization": f"Bearer {token}",
        "x-request-id": "wave5-auth-me",
    }
    me_resp = client.get("/api/auth/me", headers=me_headers)
    assert me_resp.status_code == 200, me_resp.text
    me_data = me_resp.json()
    assert me_data["user"]["username"] == login_payload["username"]

    logout_resp = client.post(
        "/api/auth/logout",
        headers={
            "x-session-id": auth_headers["x-session-id"],
            "authorization": f"Bearer {token}",
            "x-request-id": "wave5-auth-logout",
        },
    )
    assert logout_resp.status_code == 200, logout_resp.text

    me_after_logout = client.get("/api/auth/me", headers=me_headers)
    assert me_after_logout.status_code == 401


def test_wave5_session_isolation_history_and_interview(monkeypatch) -> None:
    monkeypatch.setenv("CAREER_HERO_SESSION_ISOLATION_ENABLED", "true")

    headers_a = {"x-session-id": "wave5-session-a", "x-request-id": "wave5-iso-a"}
    headers_b = {"x-session-id": "wave5-session-b", "x-request-id": "wave5-iso-b"}

    analyze_a = client.post("/api/analyze", json=make_payload(701), headers=headers_a)
    assert analyze_a.status_code == 200
    history_id_a = analyze_a.json()["historyId"]

    analyze_b = client.post("/api/analyze", json=make_payload(702), headers=headers_b)
    assert analyze_b.status_code == 200
    history_id_b = analyze_b.json()["historyId"]

    history_a = client.get("/api/history?limit=20", headers=headers_a)
    assert history_a.status_code == 200
    ids_a = {item["id"] for item in history_a.json()["items"]}
    assert history_id_a in ids_a
    assert history_id_b not in ids_a

    history_b = client.get("/api/history?limit=20", headers=headers_b)
    assert history_b.status_code == 200
    ids_b = {item["id"] for item in history_b.json()["items"]}
    assert history_id_b in ids_b
    assert history_id_a not in ids_b

    foreign_history_detail = client.get(f"/api/history/{history_id_a}", headers=headers_b)
    assert foreign_history_detail.status_code == 404
    assert_error_shape(foreign_history_detail.json())

    interview_a = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：Python FastAPI SQL Docker Redis",
            "resumeText": "5年后端开发经验，熟悉 Python FastAPI SQL Docker。",
            "questionCount": 3,
        },
        headers=headers_a,
    )
    assert interview_a.status_code == 200
    interview_id_a = interview_a.json()["session"]["id"]

    interview_b = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：Python FastAPI SQL Docker Redis",
            "resumeText": "4年后端开发经验，熟悉 Python FastAPI SQL Docker。",
            "questionCount": 3,
        },
        headers=headers_b,
    )
    assert interview_b.status_code == 200
    interview_id_b = interview_b.json()["session"]["id"]

    list_a = client.get("/api/interview/sessions?limit=20", headers=headers_a)
    assert list_a.status_code == 200
    ids_interview_a = {item["id"] for item in list_a.json()["items"]}
    assert interview_id_a in ids_interview_a
    assert interview_id_b not in ids_interview_a

    list_b = client.get("/api/interview/sessions?limit=20", headers=headers_b)
    assert list_b.status_code == 200
    ids_interview_b = {item["id"] for item in list_b.json()["items"]}
    assert interview_id_b in ids_interview_b
    assert interview_id_a not in ids_interview_b

    foreign_interview_detail = client.get(f"/api/interview/sessions/{interview_id_a}", headers=headers_b)
    assert foreign_interview_detail.status_code == 404
    assert_error_shape(foreign_interview_detail.json())


def test_wave5_knowledge_update_delete_contract_and_flow() -> None:
    spec = _openapi()
    path, update_method, delete_method = _find_knowledge_update_delete_path(spec)
    assert path and update_method and delete_method, (
        "Wave5 blocker: knowledge update/delete APIs missing. "
        "Require PUT/PATCH + DELETE on /api/rag/knowledge/{id}."
    )

    create_resp = client.post(
        "/api/rag/knowledge",
        json={
            "title": "Wave5 Knowledge Base v1",
            "content": "使用 Redis + FastAPI 进行缓存与限流。",
            "tags": ["redis", "fastapi"],
            "source": "manual",
        },
    )
    assert create_resp.status_code == 200
    created_item = create_resp.json()["item"]
    knowledge_id = created_item["id"]

    update_operation = spec["paths"][path][update_method]
    update_schema = _extract_json_schema(spec, update_operation)
    update_payload = _build_min_value(spec, update_schema) if update_schema else {}
    if not isinstance(update_payload, dict):
        update_payload = {}

    update_payload.update(
        {
            "title": "Wave5 Knowledge Base v2",
            "content": "新增 SQL 索引优化与删除策略。",
            "tags": ["redis", "sql", "optimization"],
            "source": "manual",
        }
    )

    update_url = _fill_session_path(path, knowledge_id)
    update_resp = client.request(update_method.upper(), update_url, json=update_payload)
    assert update_resp.status_code in {200, 201, 204}, update_resp.text

    list_resp = client.get("/api/rag/knowledge?limit=50")
    assert list_resp.status_code == 200
    listed = [item for item in list_resp.json()["items"] if item["id"] == knowledge_id]
    assert listed, f"knowledge item {knowledge_id} should exist after update"

    if update_resp.status_code != 204 and update_resp.headers.get("content-type", "").startswith("application/json"):
        body = update_resp.json()
        if isinstance(body, dict) and "item" in body:
            assert body["item"]["id"] == knowledge_id

    delete_resp = client.request(delete_method.upper(), update_url)
    assert delete_resp.status_code in {200, 204}, delete_resp.text

    list_after_delete = client.get("/api/rag/knowledge?limit=50")
    assert list_after_delete.status_code == 200
    assert all(item["id"] != knowledge_id for item in list_after_delete.json()["items"])


def test_wave5_interview_history_finished_sessions() -> None:
    owner_headers = {
        "x-session-id": "wave5-interview-owner-1",
        "x-request-id": "wave5-interview-history-create",
    }

    create_resp = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：Python FastAPI SQL Docker Redis",
            "resumeText": "4年后端开发经验，熟悉 Python FastAPI SQL Docker。",
            "questionCount": 3,
        },
        headers=owner_headers,
    )
    assert create_resp.status_code == 200

    session_id = create_resp.json()["session"]["id"]
    assert isinstance(session_id, int)

    answer_resp = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={
            "answerText": "我会先做瓶颈定位，再补充 Redis 缓存、SQL 索引和压测数据。",
            "questionIndex": 0,
        },
        headers={"x-session-id": owner_headers["x-session-id"], "x-request-id": "wave5-interview-history-answer"},
    )
    assert answer_resp.status_code == 200

    finish_resp = client.post(
        f"/api/interview/session/{session_id}/finish",
        headers={"x-session-id": owner_headers["x-session-id"], "x-request-id": "wave5-interview-history-finish"},
    )
    assert finish_resp.status_code == 200
    finish_payload = finish_resp.json()
    assert finish_payload["session"]["status"] == "finished"
    assert isinstance(finish_payload["feedbackDraft"]["overallScore"], int)

    history_resp = client.get(
        "/api/interview/sessions?status=finished&limit=20",
        headers={"x-session-id": owner_headers["x-session-id"], "x-request-id": "wave5-interview-history-list"},
    )
    assert history_resp.status_code == 200
    history_items = history_resp.json()["items"]
    target = next((item for item in history_items if item["id"] == session_id), None)
    assert target is not None, "finished interview session should appear in interview history list"
    assert target["status"] == "finished"
    assert target["answeredCount"] >= 1

    detail_resp = client.get(
        f"/api/interview/sessions/{session_id}",
        headers={"x-session-id": owner_headers["x-session-id"], "x-request-id": "wave5-interview-history-detail"},
    )
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["session"]["status"] == "finished"
    assert detail_data.get("feedbackDraft") is not None
