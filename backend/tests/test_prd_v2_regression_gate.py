from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.auth_store import upsert_local_account
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "career_hero_prd_v2_regression.sqlite3"
    monkeypatch.setenv("CAREER_HERO_DB_PATH", str(db_path))
    monkeypatch.setenv("CAREER_HERO_AI_PROVIDER", "rule")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("CAREER_HERO_AUTH_MODE", "local")
    monkeypatch.setenv("CAREER_HERO_REQUIRE_LOGIN_FOR_PROTECTED", "false")
    monkeypatch.setattr(
        main_module,
        "RATE_LIMITER",
        main_module.SessionRateLimiter(
            limit=30,
            window_seconds=60,
            duplicate_limit=10,
            duplicate_window_seconds=30,
        ),
    )
    monkeypatch.setattr(main_module, "METRICS", main_module.MetricsTracker())
    monkeypatch.setattr(main_module, "DEFAULT_HISTORY_RETENTION", 500)


def _assert_error_shape(data: dict[str, Any]) -> None:
    assert isinstance(data.get("code"), str) and data["code"]
    assert isinstance(data.get("message"), str) and data["message"]
    assert isinstance(data.get("requestId"), str) and data["requestId"]


def _auth_headers(session_id: str) -> dict[str, str]:
    upsert_local_account(username="prd_v2", password="prd_v2_123456")
    login_resp = client.post(
        "/api/auth/login",
        json={"username": "prd_v2", "password": "prd_v2_123456"},
        headers={"x-session-id": session_id, "x-request-id": f"{session_id}-login"},
    )
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["token"]
    return {
        "x-session-id": session_id,
        "x-session-token": token,
        "authorization": f"Bearer {token}",
    }


def _create_resume(*, title: str, content: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.post("/api/resumes", json={"title": title, "content": content}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["item"]


def _update_resume(
    *,
    resume_id: int,
    content: str,
    create_new_version: bool,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.put(
        f"/api/resumes/{resume_id}",
        json={"content": content, "createNewVersion": create_new_version},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["item"]


def _analyze_with_resume(
    *,
    resume_id: int,
    jd_text: str,
    version_no: int | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"resumeId": resume_id, "jdText": jd_text}
    if version_no is not None:
        payload["versionNo"] = version_no

    response = client.post("/api/analyze", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _history_detail(history_id: int, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.get(f"/api/history/{history_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["item"]


def test_resume_selection_defaults_to_latest_diagnostic_version() -> None:
    """PRD回归：选简历后应进入该简历最新诊断步骤（默认最新版本）。"""

    headers = _auth_headers("prd-v2-latest-version")

    created = _create_resume(title="后端简历", content="版本1：Python FastAPI 基础经验", headers=headers)
    resume_id = created["id"]

    updated = _update_resume(
        resume_id=resume_id,
        content="版本2：Python FastAPI Redis SQL 监控优化项目经验",
        create_new_version=True,
        headers=headers,
    )
    assert updated["latestVersionNo"] == 2

    latest_default = _analyze_with_resume(
        resume_id=resume_id,
        jd_text="岗位要求：Python FastAPI Redis SQL 监控",
        headers=headers,
    )
    latest_explicit = _analyze_with_resume(
        resume_id=resume_id,
        version_no=2,
        jd_text="岗位要求：Python FastAPI Redis SQL 监控",
        headers=headers,
    )
    old_version = _analyze_with_resume(
        resume_id=resume_id,
        version_no=1,
        jd_text="岗位要求：Python FastAPI Redis SQL 监控",
        headers=headers,
    )

    latest_default_detail = _history_detail(latest_default["historyId"], headers=headers)
    latest_explicit_detail = _history_detail(latest_explicit["historyId"], headers=headers)
    old_version_detail = _history_detail(old_version["historyId"], headers=headers)

    assert latest_default_detail["resumeTextHashOrExcerpt"] == latest_explicit_detail["resumeTextHashOrExcerpt"]
    assert latest_default_detail["resumeTextHashOrExcerpt"] != old_version_detail["resumeTextHashOrExcerpt"]


def test_multi_resume_progress_isolation_no_crosstalk() -> None:
    """PRD回归：多简历并行进度不串扰。"""

    headers = _auth_headers("prd-v2-multi-resume-owner")

    resume_a = _create_resume(title="简历A", content="简历A：后端开发，性能优化", headers=headers)
    resume_b = _create_resume(title="简历B", content="简历B：数据分析，BI 报表", headers=headers)

    create_a = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位A：后端开发",
            "resumeId": resume_a["id"],
            "questionCount": 3,
        },
        headers=headers,
    )
    assert create_a.status_code == 200, create_a.text
    session_a = create_a.json()["session"]["id"]

    create_b = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位B：数据分析",
            "resumeId": resume_b["id"],
            "questionCount": 3,
        },
        headers=headers,
    )
    assert create_b.status_code == 200, create_b.text
    session_b = create_b.json()["session"]["id"]

    answer_a = client.post(
        f"/api/interview/session/{session_a}/answer",
        json={"answerText": "我会先做容量评估和压测定位", "questionIndex": 0},
        headers=headers,
    )
    assert answer_a.status_code == 200, answer_a.text

    detail_a_after_answer = client.get(f"/api/interview/sessions/{session_a}", headers=headers)
    detail_b_after_answer = client.get(f"/api/interview/sessions/{session_b}", headers=headers)
    assert detail_a_after_answer.status_code == 200
    assert detail_b_after_answer.status_code == 200

    assert detail_a_after_answer.json()["session"]["answeredCount"] == 1
    assert detail_b_after_answer.json()["session"]["answeredCount"] == 0

    answer_b = client.post(
        f"/api/interview/session/{session_b}/answer",
        json={"answerText": "我会先做指标定义再设计看板", "questionIndex": 0},
        headers=headers,
    )
    assert answer_b.status_code == 200, answer_b.text

    detail_a_final = client.get(f"/api/interview/sessions/{session_a}", headers=headers)
    detail_b_final = client.get(f"/api/interview/sessions/{session_b}", headers=headers)

    assert detail_a_final.status_code == 200
    assert detail_b_final.status_code == 200
    assert detail_a_final.json()["session"]["answeredCount"] == 1
    assert detail_b_final.json()["session"]["answeredCount"] == 1


def test_interview_start_allows_degraded_entry_without_resume_text() -> None:
    """PRD回归：面试启动可进入会话（简历恢复异常时允许降级为 JD-only）。"""

    headers = _auth_headers("prd-v2-interview-start")

    response = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：后端开发，能独立交付服务",
            "questionCount": 3,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["session"]["status"] == "active"
    assert payload["session"]["questionCount"] == 3
    assert payload.get("nextQuestion") is not None


def test_chat_answer_lock_and_value_persistence_when_paused() -> None:
    """PRD回归：聊天输入在暂停状态被锁定，已提交值保持不丢失。"""

    headers = _auth_headers("prd-v2-chat-lock")
    created = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：后端开发",
            "resumeText": "5年 FastAPI 经验",
            "questionCount": 3,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]

    first_answer = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={"answerText": "先定位瓶颈，再做缓存与索引优化", "questionIndex": 0},
        headers=headers,
    )
    assert first_answer.status_code == 200, first_answer.text
    assert first_answer.json()["session"]["answeredCount"] == 1

    paused = client.post(f"/api/interview/session/{session_id}/pause", headers=headers)
    assert paused.status_code == 200, paused.text
    assert paused.json()["session"]["status"] == "paused"

    blocked = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={"answerText": "暂停状态不应被接受", "questionIndex": 1},
        headers=headers,
    )
    assert blocked.status_code == 400
    _assert_error_shape(blocked.json())

    detail_while_paused = client.get(f"/api/interview/sessions/{session_id}", headers=headers)
    assert detail_while_paused.status_code == 200
    assert detail_while_paused.json()["session"]["status"] == "paused"
    assert detail_while_paused.json()["session"]["answeredCount"] == 1

    resumed = client.post(f"/api/interview/session/{session_id}/resume", headers=headers)
    assert resumed.status_code == 200

    second_answer = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={"answerText": "继续答题：补充指标和结果", "questionIndex": 1},
        headers=headers,
    )
    assert second_answer.status_code == 200, second_answer.text
    assert second_answer.json()["session"]["answeredCount"] == 2


def test_reenter_from_list_keeps_lock_state_correct() -> None:
    """PRD回归：返回列表后再进入，会话锁定状态仍正确。"""

    headers = _auth_headers("prd-v2-reenter-lock")
    created = client.post(
        "/api/interview/session/create",
        json={
            "jdText": "岗位要求：平台工程",
            "resumeText": "熟悉 Python 与 DevOps",
            "questionCount": 3,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]

    paused = client.post(f"/api/interview/session/{session_id}/pause", headers=headers)
    assert paused.status_code == 200

    listed = client.get("/api/interview/sessions?limit=20", headers=headers)
    assert listed.status_code == 200
    listed_item = next((item for item in listed.json()["items"] if item["id"] == session_id), None)
    assert listed_item is not None
    assert listed_item["status"] == "paused"

    detail = client.get(f"/api/interview/sessions/{session_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["session"]["status"] == "paused"

    blocked_answer = client.post(
        f"/api/interview/session/{session_id}/answer",
        json={"answerText": "锁定状态不应允许提交", "questionIndex": 0},
        headers=headers,
    )
    assert blocked_answer.status_code == 400
    _assert_error_shape(blocked_answer.json())


def test_resume_list_exposes_timestamp_for_last_modified_display() -> None:
    """PRD回归：列表必须返回可用于“上次修改”展示的时间字段。"""

    headers = _auth_headers("prd-v2-last-modified")

    _create_resume(title="时间字段校验简历", content="用于校验上次修改时间字段", headers=headers)

    listed = client.get("/api/resumes?limit=20", headers=headers)
    assert listed.status_code == 200

    items = listed.json().get("items", [])
    assert items, "resume list should not be empty"

    first = items[0]
    assert isinstance(first.get("updatedAt"), str) and first["updatedAt"], "updatedAt is required for UI last-modified display"
