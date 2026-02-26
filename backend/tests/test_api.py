from __future__ import annotations

import json
import logging
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.auth_store import upsert_local_account
from app.main import AnalysisInsights, AnalysisResult, ScoreBreakdown, app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "career_hero_test.sqlite3"
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


def assert_error_shape(data: dict, *, expected_code: str) -> None:
    assert data["code"] == expected_code
    assert isinstance(data["message"], str)
    assert data["message"]
    assert isinstance(data["requestId"], str)
    assert data["requestId"]


def make_payload(i: int = 1) -> dict[str, str]:
    return {
        "resumeText": f"候选人{i}：5年Python FastAPI SQL Docker经验，做过日志与监控。",
        "jdText": "岗位要求：Python FastAPI SQL Docker Redis 监控",
    }


def parse_iso8601(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def login_local_user(*, username: str, password: str, session_id: str, request_id: str) -> dict:
    login_resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"x-session-id": session_id, "x-request-id": request_id},
    )
    assert login_resp.status_code == 200
    data = login_resp.json()
    assert isinstance(data.get("token"), str)
    assert data["token"]
    return data


def test_health() -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_analyze_success_writes_history_and_detail() -> None:
    analyze_request_id = "req-day4-1"
    analyze_resp = client.post(
        "/api/analyze",
        json=make_payload(),
        headers={"x-request-id": analyze_request_id, "x-session-id": "session-a"},
    )
    assert analyze_resp.status_code == 200
    data = analyze_resp.json()

    assert isinstance(data["score"], int)
    assert isinstance(data["historyId"], int)
    assert data["requestId"] == analyze_request_id
    assert data["analysisSource"] in {"rule", "gemini"}
    assert isinstance(data["fallbackUsed"], bool)
    assert data["promptVersion"] == main_module.PROMPT_VERSION

    history_resp = client.get("/api/history?limit=20", headers={"x-request-id": "req-history-read-1"})
    assert history_resp.status_code == 200
    history_data = history_resp.json()
    assert history_data["requestId"] == "req-history-read-1"
    assert history_data["total"] == 1
    assert len(history_data["items"]) == 1

    first_item = history_data["items"][0]
    assert first_item["id"] == data["historyId"]
    assert first_item["requestId"] == analyze_request_id
    assert "sha256:" in first_item["resumeTextHashOrExcerpt"]

    detail_resp = client.get(f"/api/history/{data['historyId']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["item"]
    assert isinstance(detail["matchedKeywords"], list)
    assert isinstance(detail["missingKeywords"], list)
    assert isinstance(detail["suggestions"], list)
    assert isinstance(detail["optimizedResume"], str)
    assert isinstance(detail["insights"]["summary"], str)


def test_history_request_id_filter_and_default_limit() -> None:
    session_headers = {"x-session-id": "session-history-batch"}

    for i in range(25):
        resp = client.post(
            "/api/analyze",
            json=make_payload(i),
            headers={**session_headers, "x-request-id": f"req-history-{i}"},
        )
        assert resp.status_code == 200

    default_limit_resp = client.get("/api/history", headers=session_headers)
    assert default_limit_resp.status_code == 200
    default_items = default_limit_resp.json()["items"]
    assert len(default_items) == 20

    filtered = client.get("/api/history?requestId=req-history-7", headers=session_headers)
    assert filtered.status_code == 200
    filtered_data = filtered.json()
    assert filtered_data["total"] == 1
    assert len(filtered_data["items"]) == 1
    assert filtered_data["items"][0]["requestId"] == "req-history-7"


def test_history_cleanup_keep_latest_and_delete_all() -> None:
    for i in range(5):
        resp = client.post("/api/analyze", json=make_payload(i))
        assert resp.status_code == 200

    keep_resp = client.post(
        "/api/history/cleanup",
        json={"mode": "keep_latest", "keepLatest": 2},
    )
    assert keep_resp.status_code == 200
    keep_data = keep_resp.json()
    assert keep_data["deleted"] == 3
    assert keep_data["total"] == 2

    reject_resp = client.post("/api/history/cleanup", json={"mode": "delete_all"})
    assert reject_resp.status_code == 400
    assert_error_shape(reject_resp.json(), expected_code="BAD_REQUEST")

    delete_resp = client.post(
        "/api/history/cleanup",
        json={"mode": "delete_all", "confirmText": "DELETE"},
    )
    assert delete_resp.status_code == 200
    assert delete_resp.json()["total"] == 0


def test_export_txt_json_pdf() -> None:
    resp = client.post("/api/analyze", json=make_payload())
    assert resp.status_code == 200
    history_id = resp.json()["historyId"]

    txt = client.get(f"/api/history/{history_id}/export?format=txt")
    assert txt.status_code == 200
    assert "attachment; filename=" in txt.headers.get("content-disposition", "")
    assert "Career Hero" in txt.text

    js = client.get(f"/api/history/{history_id}/export?format=json")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("application/json")
    payload = json.loads(js.text)
    assert payload["id"] == history_id

    pdf = client.get(f"/api/history/{history_id}/export?format=pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert len(pdf.content) > 100


def test_analyze_validation_bad_request_and_extra_field_errors() -> None:
    validation_resp = client.post("/api/analyze", json={"resumeText": "", "jdText": ""})
    assert validation_resp.status_code == 422
    assert_error_shape(validation_resp.json(), expected_code="VALIDATION_ERROR")

    bad_request_resp = client.post("/api/analyze", json={"resumeText": "a", "jdText": "b"})
    assert bad_request_resp.status_code == 400
    assert_error_shape(bad_request_resp.json(), expected_code="BAD_REQUEST")

    extra_field_resp = client.post(
        "/api/analyze",
        json={"resumeText": "Python", "jdText": "Python", "unexpected": "x"},
    )
    assert extra_field_resp.status_code == 422
    assert_error_shape(extra_field_resp.json(), expected_code="VALIDATION_ERROR")


def test_payload_too_large() -> None:
    original = main_module.MAX_JSON_BODY_BYTES
    main_module.MAX_JSON_BODY_BYTES = 200
    try:
        payload = {
            "resumeText": "Python " * 80,
            "jdText": "FastAPI SQL " * 40,
        }
        resp = client.post("/api/analyze", json=payload)
    finally:
        main_module.MAX_JSON_BODY_BYTES = original

    assert resp.status_code == 413
    assert_error_shape(resp.json(), expected_code="PAYLOAD_TOO_LARGE")


def test_rate_limit_and_duplicate_protection(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "RATE_LIMITER",
        main_module.SessionRateLimiter(
            limit=3,
            window_seconds=60,
            duplicate_limit=2,
            duplicate_window_seconds=60,
        ),
    )

    headers = {"x-session-id": "session-rate-1"}
    payload = make_payload(1)

    ok1 = client.post("/api/analyze", json=payload, headers=headers)
    assert ok1.status_code == 200
    ok2 = client.post("/api/analyze", json=payload, headers=headers)
    assert ok2.status_code == 200

    duplicate_block = client.post("/api/analyze", json=payload, headers=headers)
    assert duplicate_block.status_code == 429
    assert_error_shape(duplicate_block.json(), expected_code="TOO_MANY_REQUESTS")


def test_gemini_fallback_to_rule(monkeypatch) -> None:
    monkeypatch.setenv("CAREER_HERO_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def fake_fail(*_args, **_kwargs):
        raise RuntimeError("gemini unavailable")

    monkeypatch.setattr(main_module, "call_gemini_analysis", fake_fail)

    resp = client.post("/api/analyze", json=make_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["analysisSource"] == "rule"
    assert data["fallbackUsed"] is True


def test_gemini_success_path(monkeypatch) -> None:
    monkeypatch.setenv("CAREER_HERO_AI_PROVIDER", "auto")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def fake_success(*_args, **_kwargs):
        return AnalysisResult(
            score=88,
            matched_keywords=["python", "fastapi"],
            missing_keywords=["redis"],
            suggestions=["补充 Redis 项目实践"],
            optimized_resume="优化后简历",
            score_breakdown=ScoreBreakdown(keyword_match=90, coverage=85, writing_quality_stub=70),
            insights=AnalysisInsights(summary="匹配较高", strengths=["后端经验"], risks=["缺少缓存项目"]),
            analysis_source="gemini",
            fallback_used=False,
        )

    monkeypatch.setattr(main_module, "call_gemini_analysis", fake_success)

    resp = client.post("/api/analyze", json=make_payload())
    assert resp.status_code == 200
    data = resp.json()
    assert data["analysisSource"] == "gemini"
    assert data["fallbackUsed"] is False
    assert data["score"] == 88


def test_internal_error_shape(monkeypatch) -> None:
    def raise_runtime_error(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main_module, "compute_rule_based_analysis", raise_runtime_error)

    resp = client.post("/api/analyze", json=make_payload())
    assert resp.status_code == 500
    assert_error_shape(resp.json(), expected_code="INTERNAL_ERROR")


def test_observability_log_contains_fields(caplog) -> None:
    request_id = "req-observability-1"

    with caplog.at_level(logging.INFO, logger="career_hero.api"):
        resp = client.get("/health", headers={"x-request-id": request_id, "x-session-id": "s-ob-1"})

    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == request_id
    assert resp.headers.get("x-session-id") == "s-ob-1"

    parsed_logs: list[dict] = []
    for record in caplog.records:
        try:
            parsed_logs.append(json.loads(record.getMessage()))
        except json.JSONDecodeError:
            continue

    health_log = next((item for item in parsed_logs if item.get("path") == "/health"), None)
    assert health_log is not None
    assert health_log["method"] == "GET"
    assert health_log["status"] == 200
    assert isinstance(health_log["duration_ms"], int)
    assert health_log["requestId"] == request_id
    assert health_log["sessionId"] == "s-ob-1"
    assert "error_code" in health_log
    assert "exception_type" in health_log


def test_metrics_snapshot_contains_error_classification() -> None:
    ok = client.get("/health")
    assert ok.status_code == 200

    bad = client.post("/api/analyze", json={"resumeText": "a", "jdText": "b"})
    assert bad.status_code == 400

    snapshot = client.get("/api/metrics/snapshot")
    assert snapshot.status_code == 200
    data = snapshot.json()

    assert data["requestTotal"] >= 2
    assert isinstance(data["pathCounts"], dict)
    assert isinstance(data["statusCounts"], dict)
    assert isinstance(data["errorCounts"], dict)
    assert data["statusCounts"].get("400", 0) >= 1
    assert data["errorCounts"].get("BAD_REQUEST", 0) >= 1


def test_minimal_e2e_chain_analyze_history_detail_export() -> None:
    analyze = client.post("/api/analyze", json=make_payload(), headers={"x-session-id": "session-e2e"})
    assert analyze.status_code == 200
    history_id = analyze.json()["historyId"]

    history = client.get("/api/history?limit=5")
    assert history.status_code == 200
    assert any(item["id"] == history_id for item in history.json()["items"])

    detail = client.get(f"/api/history/{history_id}")
    assert detail.status_code == 200
    assert detail.json()["item"]["id"] == history_id

    export = client.get(f"/api/history/{history_id}/export?format=txt")
    assert export.status_code == 200
    assert "Career Hero" in export.text


def test_analyze_validation_blocked_pattern() -> None:
    resp = client.post(
        "/api/analyze",
        json={"resumeText": "<script>alert('x')</script>", "jdText": "Python FastAPI"},
    )
    assert resp.status_code == 422
    assert_error_shape(resp.json(), expected_code="VALIDATION_ERROR")


def test_analyze_rate_limit_window_exceeded_returns_429(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "RATE_LIMITER",
        main_module.SessionRateLimiter(
            limit=2,
            window_seconds=60,
            duplicate_limit=99,
            duplicate_window_seconds=60,
        ),
    )

    headers = {"x-session-id": "session-limit-window"}
    assert client.post("/api/analyze", json=make_payload(1), headers=headers).status_code == 200
    assert client.post("/api/analyze", json=make_payload(2), headers=headers).status_code == 200

    blocked = client.post("/api/analyze", json=make_payload(3), headers=headers)
    assert blocked.status_code == 429
    assert_error_shape(blocked.json(), expected_code="TOO_MANY_REQUESTS")
    assert blocked.headers.get("x-ratelimit-limit") == "2"


def test_payload_too_large_short_circuit_has_request_id() -> None:
    original = main_module.MAX_JSON_BODY_BYTES
    main_module.MAX_JSON_BODY_BYTES = 64
    try:
        resp = client.post(
            "/api/analyze",
            json=make_payload(),
            headers={"x-request-id": "req-413-short", "x-session-id": "session-413-short"},
        )
    finally:
        main_module.MAX_JSON_BODY_BYTES = original

    assert resp.status_code == 413
    data = resp.json()
    assert_error_shape(data, expected_code="PAYLOAD_TOO_LARGE")
    assert data["requestId"] == "req-413-short"
    assert resp.headers.get("x-session-id") == "session-413-short"


def test_history_list_limit_is_capped(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "MAX_HISTORY_LIMIT", 5)

    for i in range(8):
        resp = client.post("/api/analyze", json=make_payload(i))
        assert resp.status_code == 200

    history = client.get("/api/history?limit=999")
    assert history.status_code == 200
    payload = history.json()
    assert payload["total"] == 8
    assert len(payload["items"]) == 5


def test_history_detail_invalid_and_not_found() -> None:
    invalid = client.get("/api/history/0")
    assert invalid.status_code == 400
    assert_error_shape(invalid.json(), expected_code="BAD_REQUEST")

    missing = client.get("/api/history/9999")
    assert missing.status_code == 404
    assert_error_shape(missing.json(), expected_code="NOT_FOUND")


def test_history_cleanup_validation_error_keep_latest_bounds() -> None:
    resp = client.post("/api/history/cleanup", json={"mode": "keep_latest", "keepLatest": 0})
    assert resp.status_code == 422
    assert_error_shape(resp.json(), expected_code="VALIDATION_ERROR")


def test_history_export_not_found_and_invalid_format() -> None:
    missing = client.get("/api/history/9999/export?format=txt")
    assert missing.status_code == 404
    assert_error_shape(missing.json(), expected_code="NOT_FOUND")

    invalid = client.get("/api/history/1/export?format=xml")
    assert invalid.status_code == 422
    assert_error_shape(invalid.json(), expected_code="VALIDATION_ERROR")


def test_metrics_snapshot_contains_latency_schema_and_request_id() -> None:
    assert client.get("/health").status_code == 200
    assert client.post("/api/analyze", json=make_payload()).status_code == 200

    snapshot = client.get("/api/metrics/snapshot", headers={"x-request-id": "req-metrics-schema"})
    assert snapshot.status_code == 200
    data = snapshot.json()

    assert data["requestId"] == "req-metrics-schema"
    assert isinstance(datetime.fromisoformat(data["generatedAt"]), datetime)
    assert "/health" in data["latency"]
    assert "/api/analyze" in data["latency"]

    for entry in data["latency"].values():
        assert isinstance(entry["count"], int)
        assert isinstance(entry["p50_ms"], int)
        assert isinstance(entry["p95_ms"], int)


def test_resume_crud_and_versioning() -> None:
    create_resp = client.post(
        "/api/resumes",
        json={"title": "后端工程师简历", "content": "3年 FastAPI + SQLite 经验"},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["item"]
    resume_id = created["id"]

    assert created["latestVersionNo"] == 1
    assert len(created["versions"]) == 1
    assert created["currentVersion"]["versionNo"] == 1

    list_resp = client.get("/api/resumes")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert any(item["id"] == resume_id for item in items)

    update_resp = client.put(
        f"/api/resumes/{resume_id}",
        json={"content": "4年 FastAPI + SQLite + Docker 经验", "createNewVersion": True},
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()["item"]
    assert updated["latestVersionNo"] == 2
    assert len(updated["versions"]) == 2
    assert updated["currentVersion"]["content"].startswith("4年")

    delete_resp = client.delete(f"/api/resumes/{resume_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    list_after_delete = client.get("/api/resumes")
    assert list_after_delete.status_code == 200
    assert not any(item["id"] == resume_id for item in list_after_delete.json()["items"])


def test_resume_update_without_new_version_overwrites_latest() -> None:
    create_resp = client.post(
        "/api/resumes",
        json={"title": "算法简历", "content": "熟悉 Python 与算法"},
    )
    assert create_resp.status_code == 200
    resume_id = create_resp.json()["item"]["id"]

    update_resp = client.put(
        f"/api/resumes/{resume_id}",
        json={"content": "熟悉 Python 算法与系统设计", "createNewVersion": False},
    )
    assert update_resp.status_code == 200
    item = update_resp.json()["item"]

    assert item["latestVersionNo"] == 1
    assert len(item["versions"]) == 1
    assert "系统设计" in item["currentVersion"]["content"]


def test_resume_endpoints_validation_and_not_found() -> None:
    invalid_create = client.post("/api/resumes", json={"title": "", "content": "x"})
    assert invalid_create.status_code == 422
    assert_error_shape(invalid_create.json(), expected_code="VALIDATION_ERROR")

    missing_get = client.get("/api/resumes/999")
    assert missing_get.status_code == 404
    assert_error_shape(missing_get.json(), expected_code="NOT_FOUND")

    bad_id_get = client.get("/api/resumes/0")
    assert bad_id_get.status_code == 400
    assert_error_shape(bad_id_get.json(), expected_code="BAD_REQUEST")

    empty_update = client.put("/api/resumes/1", json={})
    assert empty_update.status_code == 400
    assert_error_shape(empty_update.json(), expected_code="BAD_REQUEST")

    missing_delete = client.delete("/api/resumes/999")
    assert missing_delete.status_code == 404
    assert_error_shape(missing_delete.json(), expected_code="NOT_FOUND")


def test_analyze_rag_mode_switch_and_topk() -> None:
    knowledge_payloads = [
        {
            "title": "Redis 高并发实践",
            "content": "使用 Redis + FastAPI 构建限流与缓存，提升接口稳定性与吞吐。",
            "tags": ["redis", "fastapi", "backend"],
            "source": "manual",
        },
        {
            "title": "监控告警体系",
            "content": "Prometheus + Grafana 监控 Python 服务，覆盖 Docker 部署环境。",
            "tags": ["monitoring", "docker", "python"],
            "source": "manual",
        },
        {
            "title": "SQL 性能优化",
            "content": "针对 SQL 查询慢问题进行索引优化，并结合 Redis 做热点缓存。",
            "tags": ["sql", "redis", "optimization"],
            "source": "manual",
        },
    ]

    for item in knowledge_payloads:
        created = client.post("/api/rag/knowledge", json=item)
        assert created.status_code == 200

    base_payload = make_payload(401)

    rag_off = client.post(
        "/api/analyze",
        json={**base_payload, "ragEnabled": False},
        headers={"x-session-id": "session-rag-switch-off", "x-request-id": "req-rag-switch-off"},
    )
    assert rag_off.status_code == 200
    rag_off_data = rag_off.json()
    assert rag_off_data["ragEnabled"] is False
    assert rag_off_data["ragHits"] == []

    rag_on_top1 = client.post(
        "/api/analyze",
        json={**base_payload, "ragEnabled": True, "ragTopK": 1},
        headers={"x-session-id": "session-rag-switch-on-1", "x-request-id": "req-rag-switch-on-1"},
    )
    assert rag_on_top1.status_code == 200
    top1_hits = rag_on_top1.json()["ragHits"]
    assert len(top1_hits) <= 1

    rag_on_top3 = client.post(
        "/api/analyze",
        json={**base_payload, "ragEnabled": True, "ragTopK": 3},
        headers={"x-session-id": "session-rag-switch-on-3", "x-request-id": "req-rag-switch-on-3"},
    )
    assert rag_on_top3.status_code == 200
    rag_on_data = rag_on_top3.json()
    top3_hits = rag_on_data["ragHits"]

    assert rag_on_data["ragEnabled"] is True
    assert 0 <= len(top3_hits) <= 3
    assert len(top3_hits) >= len(top1_hits)

    if top3_hits:
        hit = top3_hits[0]
        assert isinstance(hit["id"], int)
        assert isinstance(hit["title"], str)
        assert isinstance(hit["snippet"], str)
        assert isinstance(hit["score"], (int, float))


def test_session_isolation_for_rate_limit_and_history_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "RATE_LIMITER",
        main_module.SessionRateLimiter(
            limit=2,
            window_seconds=60,
            duplicate_limit=2,
            duplicate_window_seconds=60,
        ),
    )

    payload = make_payload(501)

    s1_h1 = client.post(
        "/api/analyze",
        json=payload,
        headers={"x-session-id": "session-isolation-a", "x-request-id": "req-iso-a-1"},
    )
    assert s1_h1.status_code == 200

    s1_h2 = client.post(
        "/api/analyze",
        json=payload,
        headers={"x-session-id": "session-isolation-a", "x-request-id": "req-iso-a-2"},
    )
    assert s1_h2.status_code == 200

    s1_blocked = client.post(
        "/api/analyze",
        json=payload,
        headers={"x-session-id": "session-isolation-a", "x-request-id": "req-iso-a-3"},
    )
    assert s1_blocked.status_code == 429
    assert_error_shape(s1_blocked.json(), expected_code="TOO_MANY_REQUESTS")

    s2_ok = client.post(
        "/api/analyze",
        json=payload,
        headers={"x-session-id": "session-isolation-b", "x-request-id": "req-iso-b-1"},
    )
    assert s2_ok.status_code == 200

    first_detail = client.get(
        f"/api/history/{s1_h1.json()['historyId']}",
        headers={"x-session-id": "session-isolation-a"},
    )
    assert first_detail.status_code == 200
    first_item = first_detail.json()["item"]
    assert first_item["sessionId"] == "session-isolation-a"
    assert first_item["requestId"] == "req-iso-a-1"

    second_detail = client.get(
        f"/api/history/{s2_ok.json()['historyId']}",
        headers={"x-session-id": "session-isolation-b"},
    )
    assert second_detail.status_code == 200
    second_item = second_detail.json()["item"]
    assert second_item["sessionId"] == "session-isolation-b"
    assert second_item["requestId"] == "req-iso-b-1"


def test_interview_list_detail_pause_resume_and_answer_chain() -> None:
    session_headers = {
        "x-request-id": "req-interview-chain-create",
        "x-session-id": "session-interview-owner-a",
    }

    create_resp = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：Python FastAPI SQL Docker Redis",
            "resumeText": "3年后端开发，熟悉 Python FastAPI SQL Docker。",
            "questionCount": 3,
        },
        headers=session_headers,
    )
    assert create_resp.status_code == 200

    create_data = create_resp.json()
    session_id = create_data["session"]["id"]
    assert create_data["session"]["status"] == "active"
    assert create_data["session"]["questionCount"] == 3
    assert create_data["nextQuestion"] is not None

    list_resp = client.get("/api/interview/sessions", headers={"x-session-id": "session-interview-owner-a"})
    assert list_resp.status_code == 200
    assert any(item["id"] == session_id for item in list_resp.json()["items"])

    detail_resp = client.get(
        f"/api/interview/sessions/{session_id}",
        headers={"x-session-id": "session-interview-owner-a"},
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["session"]["id"] == session_id

    pause_resp = client.post(
        f"/api/interview/session/{session_id}/pause",
        headers={"x-session-id": "session-interview-owner-a", "x-request-id": "req-interview-pause"},
    )
    assert pause_resp.status_code == 200
    assert pause_resp.json()["session"]["status"] == "paused"

    answer_when_paused = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={"answerText": "先回答一个问题", "questionIndex": 0},
        headers={"x-session-id": "session-interview-owner-a"},
    )
    assert answer_when_paused.status_code == 400
    assert_error_shape(answer_when_paused.json(), expected_code="BAD_REQUEST")

    resume_resp = client.post(
        f"/api/interview/session/{session_id}/resume",
        headers={"x-session-id": "session-interview-owner-a", "x-request-id": "req-interview-resume"},
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["session"]["status"] == "active"

    next_resp = client.post(
        f"/api/interview/session/{session_id}/next",
        headers={"x-session-id": "session-interview-owner-a", "x-request-id": "req-interview-chain-next"},
    )
    assert next_resp.status_code == 200
    next_data = next_resp.json()
    assert next_data["session"]["id"] == session_id
    assert next_data["nextQuestion"] is not None

    answer_resp = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={"answerText": "我会先定位瓶颈并补充 Redis 缓存与压测数据。", "questionIndex": 0},
        headers={"x-session-id": "session-interview-owner-a", "x-request-id": "req-interview-chain-answer"},
    )
    assert answer_resp.status_code == 200
    answer_data = answer_resp.json()
    assert answer_data["session"]["id"] == session_id
    assert answer_data["session"]["answeredCount"] >= 1
    assert isinstance(answer_data["evaluation"]["answerScore"], int)

    finish_resp = client.post(
        f"/api/interview/session/{session_id}/finish",
        headers={"x-session-id": "session-interview-owner-a", "x-request-id": "req-interview-chain-finish"},
    )
    assert finish_resp.status_code == 200
    finish_data = finish_resp.json()
    assert finish_data["session"]["status"] == "finished"
    assert isinstance(finish_data["feedbackDraft"]["overallScore"], int)

    foreign_detail = client.get(
        f"/api/interview/sessions/{session_id}",
        headers={"x-session-id": "session-interview-owner-b", "x-request-id": "req-interview-foreign-detail"},
    )
    assert foreign_detail.status_code == 404
    assert_error_shape(foreign_detail.json(), expected_code="NOT_FOUND")


def test_auth_relogin_refresh_and_session_bound_token(monkeypatch) -> None:
    client.cookies.clear()

    monkeypatch.setenv("CAREER_HERO_DEFAULT_USERNAME", "demo")
    monkeypatch.setenv("CAREER_HERO_DEFAULT_PASSWORD", "demo123456")
    main_module.ensure_default_local_account()

    first_login = login_local_user(
        username="demo",
        password="demo123456",
        session_id="session-auth-refresh-1",
        request_id="req-auth-refresh-login-1",
    )
    refreshed_login = login_local_user(
        username="demo",
        password="demo123456",
        session_id="session-auth-refresh-1",
        request_id="req-auth-refresh-login-2",
    )

    assert refreshed_login["token"] != first_login["token"]
    assert parse_iso8601(refreshed_login["expiresAt"]) >= parse_iso8601(first_login["expiresAt"])

    me_resp = client.get(
        "/api/auth/me",
        headers={
            "x-session-id": "session-auth-refresh-1",
            "x-session-token": refreshed_login["token"],
            "x-request-id": "req-auth-refresh-me",
        },
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["user"]["username"] == "demo"

    foreign_session_resp = client.get(
        "/api/auth/me",
        headers={
            "x-session-id": "session-auth-refresh-2",
            "x-session-token": refreshed_login["token"],
            "x-request-id": "req-auth-refresh-foreign",
        },
    )
    assert foreign_session_resp.status_code == 401
    assert_error_shape(foreign_session_resp.json(), expected_code="UNAUTHORIZED")

    logout_resp = client.post(
        "/api/auth/logout",
        headers={
            "x-session-id": "session-auth-refresh-1",
            "x-session-token": refreshed_login["token"],
            "x-request-id": "req-auth-refresh-logout",
        },
    )
    assert logout_resp.status_code == 200
    assert logout_resp.json()["revoked"] is True

    me_after_logout = client.get(
        "/api/auth/me",
        headers={
            "x-session-id": "session-auth-refresh-1",
            "x-session-token": refreshed_login["token"],
            "x-request-id": "req-auth-refresh-me-after-logout",
        },
    )
    assert me_after_logout.status_code == 401
    assert_error_shape(me_after_logout.json(), expected_code="UNAUTHORIZED")


def test_authenticated_scope_isolation_for_history_and_interview() -> None:
    client.cookies.clear()

    upsert_local_account(username="alice", password="alice123")
    upsert_local_account(username="bob", password="bob12345")

    alice_login = login_local_user(
        username="alice",
        password="alice123",
        session_id="session-auth-alice",
        request_id="req-auth-alice-login",
    )
    bob_login = login_local_user(
        username="bob",
        password="bob12345",
        session_id="session-auth-bob",
        request_id="req-auth-bob-login",
    )

    alice_headers = {
        "x-session-id": "session-auth-alice",
        "x-session-token": alice_login["token"],
    }
    bob_headers = {
        "x-session-id": "session-auth-bob",
        "x-session-token": bob_login["token"],
    }

    analyze_alice = client.post(
        "/api/analyze",
        json=make_payload(801),
        headers={**alice_headers, "x-request-id": "req-auth-alice-analyze"},
    )
    assert analyze_alice.status_code == 200
    alice_history_id = analyze_alice.json()["historyId"]

    analyze_bob = client.post(
        "/api/analyze",
        json=make_payload(802),
        headers={**bob_headers, "x-request-id": "req-auth-bob-analyze"},
    )
    assert analyze_bob.status_code == 200
    bob_history_id = analyze_bob.json()["historyId"]

    history_alice = client.get("/api/history?limit=20", headers=alice_headers)
    assert history_alice.status_code == 200
    alice_history_ids = {item["id"] for item in history_alice.json()["items"]}
    assert alice_history_id in alice_history_ids
    assert bob_history_id not in alice_history_ids

    history_bob = client.get("/api/history?limit=20", headers=bob_headers)
    assert history_bob.status_code == 200
    bob_history_ids = {item["id"] for item in history_bob.json()["items"]}
    assert bob_history_id in bob_history_ids
    assert alice_history_id not in bob_history_ids

    foreign_history_detail = client.get(f"/api/history/{alice_history_id}", headers=bob_headers)
    assert foreign_history_detail.status_code == 404
    assert_error_shape(foreign_history_detail.json(), expected_code="NOT_FOUND")

    interview_alice = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：Python FastAPI SQL Docker Redis",
            "resumeText": "5年后端经验，熟悉 Python FastAPI SQL Docker。",
            "questionCount": 3,
        },
        headers={**alice_headers, "x-request-id": "req-auth-alice-interview"},
    )
    assert interview_alice.status_code == 200
    alice_interview_id = interview_alice.json()["session"]["id"]

    interview_bob = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：Python FastAPI SQL Docker Redis",
            "resumeText": "4年后端经验，熟悉 Python FastAPI SQL Docker。",
            "questionCount": 3,
        },
        headers={**bob_headers, "x-request-id": "req-auth-bob-interview"},
    )
    assert interview_bob.status_code == 200
    bob_interview_id = interview_bob.json()["session"]["id"]

    list_alice = client.get("/api/interview/sessions?limit=20", headers=alice_headers)
    assert list_alice.status_code == 200
    alice_interview_ids = {item["id"] for item in list_alice.json()["items"]}
    assert alice_interview_id in alice_interview_ids
    assert bob_interview_id not in alice_interview_ids

    foreign_interview_detail = client.get(f"/api/interview/sessions/{alice_interview_id}", headers=bob_headers)
    assert foreign_interview_detail.status_code == 404
    assert_error_shape(foreign_interview_detail.json(), expected_code="NOT_FOUND")


def test_knowledge_audit_fields_created_and_updated() -> None:
    client.cookies.clear()

    create_resp = client.post(
        "/api/rag/knowledge",
        json={
            "title": "Wave6 审计字段验证",
            "content": "初始内容：Redis + FastAPI",
            "tags": ["redis", "fastapi"],
            "source": "wave6-audit",
        },
        headers={"x-request-id": "req-wave6-knowledge-create"},
    )
    assert create_resp.status_code == 200

    created_item = create_resp.json()["item"]
    created_at_initial = parse_iso8601(created_item["createdAt"])
    updated_at_initial = parse_iso8601(created_item["updatedAt"])

    assert created_item["source"] == "wave6-audit"
    assert updated_at_initial >= created_at_initial

    time.sleep(0.02)

    update_resp = client.put(
        f"/api/rag/knowledge/{created_item['id']}",
        json={
            "content": "更新内容：Redis + FastAPI + SQL 索引优化",
            "tags": ["redis", "fastapi", "sql"],
            "source": "wave6-audit-updated",
        },
        headers={"x-request-id": "req-wave6-knowledge-update"},
    )
    assert update_resp.status_code == 200

    updated_item = update_resp.json()["item"]
    updated_at_after = parse_iso8601(updated_item["updatedAt"])

    assert updated_item["createdAt"] == created_item["createdAt"]
    assert updated_item["updatedAt"] != created_item["updatedAt"]
    assert updated_at_after >= updated_at_initial
    assert updated_item["source"] == "wave6-audit-updated"

    list_resp = client.get("/api/rag/knowledge?limit=20", headers={"x-request-id": "req-wave6-knowledge-list"})
    assert list_resp.status_code == 200

    listed_item = next((item for item in list_resp.json()["items"] if item["id"] == created_item["id"]), None)
    assert listed_item is not None
    assert listed_item["source"] == "wave6-audit-updated"
    assert parse_iso8601(listed_item["createdAt"]) == created_at_initial
    assert parse_iso8601(listed_item["updatedAt"]) >= updated_at_after


def test_interview_summary_result_chain() -> None:
    client.cookies.clear()

    headers = {
        "x-session-id": "session-interview-summary-owner",
        "x-request-id": "req-interview-summary-create",
    }

    create_resp = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：Python FastAPI SQL Docker Redis",
            "resumeText": "4年后端开发，熟悉 Python FastAPI SQL Docker。",
            "questionCount": 3,
        },
        headers=headers,
    )
    assert create_resp.status_code == 200

    session_id = create_resp.json()["session"]["id"]

    answer_resp = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={
            "answerText": "我会先量化基线，再通过 Redis 缓存和 SQL 索引优化把接口 p95 从 300ms 降到 120ms。",
            "questionIndex": 0,
        },
        headers={"x-session-id": headers["x-session-id"], "x-request-id": "req-interview-summary-answer"},
    )
    assert answer_resp.status_code == 200

    finish_resp = client.post(
        f"/api/interview/session/{session_id}/finish",
        headers={"x-session-id": headers["x-session-id"], "x-request-id": "req-interview-summary-finish"},
    )
    assert finish_resp.status_code == 200

    finish_data = finish_resp.json()
    assert finish_data["session"]["status"] == "finished"
    assert isinstance(finish_data["feedbackDraft"]["summary"], str)
    assert finish_data["feedbackDraft"]["summary"]

    result_list_resp = client.get(
        "/api/interview/results?limit=20",
        headers={"x-session-id": headers["x-session-id"], "x-request-id": "req-interview-summary-results"},
    )
    assert result_list_resp.status_code == 200
    result_item = next(
        (item for item in result_list_resp.json()["items"] if item["id"] == session_id),
        None,
    )
    assert result_item is not None
    assert result_item["status"] == "finished"
    assert result_item["summary"] == finish_data["feedbackDraft"]["summary"]
    assert result_item["finalScore"] == finish_data["feedbackDraft"]["overallScore"]

    result_detail_resp = client.get(
        f"/api/interview/results/{session_id}",
        headers={"x-session-id": headers["x-session-id"], "x-request-id": "req-interview-summary-detail"},
    )
    assert result_detail_resp.status_code == 200

    result_detail = result_detail_resp.json()
    assert result_detail["session"]["status"] == "finished"
    assert result_detail["session"]["summary"] == finish_data["feedbackDraft"]["summary"]
    assert result_detail["feedbackDraft"]["summary"] == finish_data["feedbackDraft"]["summary"]
