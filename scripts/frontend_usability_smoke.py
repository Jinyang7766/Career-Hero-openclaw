#!/usr/bin/env python3
"""Wave7 front-end usability smoke + screenshots.

This script:
1) logs in via backend API (optional)
2) opens core pages with Playwright
3) checks page-level smoke expectations
4) captures screenshots
5) outputs JSON + Markdown manifests
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import error, request

from playwright.sync_api import sync_playwright


@dataclass
class RouteSpec:
    route: str
    file_name: str
    expected_text: list[str]


@dataclass
class RouteResult:
    route: str
    url: str
    file: str
    status: str
    duration_ms: int
    expected_text: list[str]
    missing_text: list[str]
    notes: list[str]


DEFAULT_ROUTES = [
    RouteSpec("/", "01-home.png", ["Career Hero MVP", "分析输入"]),
    RouteSpec("/resumes", "02-resumes.png", ["Resume 管理", "简历列表"]),
    RouteSpec("/rag", "03-rag.png", ["RAG / 知识库管理", "知识条目列表"]),
    RouteSpec("/interview", "04-interview.png", ["Interview 练习", "开始会话"]),
    RouteSpec("/interview/summary", "05-interview-summary.png", ["面试最终总结页", "会话列表"]),
    RouteSpec("/login", "06-login.png", ["登录 / 退出", "登录表单"]),
]

CRASH_MARKERS = [
    "Application error",
    "Unhandled Runtime Error",
    "This page could not be found",
    "ERR_CONNECTION_REFUSED",
]


def http_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 20,
) -> tuple[int, dict[str, Any] | list[Any] | str]:
    body = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed: dict[str, Any] | list[Any] | str
            if raw and raw.startswith(("{", "[")):
                parsed = json.loads(raw)
            else:
                parsed = raw
            return resp.status, parsed
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed_error: dict[str, Any] | list[Any] | str = json.loads(raw)
        except Exception:
            parsed_error = raw
        return exc.code, parsed_error


def build_auth_state(
    backend_base_url: str,
    username: str,
    password: str,
    timeout_sec: int,
) -> dict[str, Any]:
    session_id = f"wave7-shot-{int(time.time())}"
    status, payload = http_json(
        f"{backend_base_url.rstrip('/')}/api/auth/login",
        method="POST",
        payload={"username": username, "password": password},
        headers={"x-session-id": session_id},
        timeout=timeout_sec,
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"login failed: {status} {payload}")

    return {
        "sessionId": payload.get("sessionId", session_id),
        "accessToken": payload.get("token", ""),
        "refreshToken": None,
        "mode": "custom",
        "updatedAt": datetime.utcnow().isoformat() + "Z",
        "userName": (payload.get("user") or {}).get("username", username),
        "expiresAt": payload.get("expiresAt"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Frontend usability smoke + screenshots")
    parser.add_argument("--frontend-base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--storage-key", default="career_hero.client_auth.v3")
    parser.add_argument("--out-root", default="screenshots")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--timeout-ms", type=int, default=45_000)
    parser.add_argument("--wait-ms", type=int, default=1_200)
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--headless", action="store_true", default=True)
    parser.add_argument("--headed", dest="headless", action="store_false")
    parser.add_argument("--no-auth", action="store_true", help="Skip login and capture as guest")
    return parser.parse_args()


def ensure_out_dir(out_root: str, out_dir: str) -> Path:
    if out_dir:
        path = Path(out_dir)
    else:
        path = Path(out_root) / f"wave7-usability-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_missing_text(page: Any, expected_text: list[str]) -> list[str]:
    missing: list[str] = []
    for text in expected_text:
        locator = page.get_by_text(text, exact=False)
        if locator.count() < 1:
            missing.append(text)
    return missing


def scan_crash_markers(page_text: str) -> list[str]:
    lowered = page_text.lower()
    found: list[str] = []
    for marker in CRASH_MARKERS:
        if marker.lower() in lowered:
            found.append(marker)
    return found


def capture_routes(args: argparse.Namespace, out_dir: Path) -> tuple[list[RouteResult], dict[str, Any]]:
    auth_state: dict[str, Any] = {}
    if not args.no_auth:
        auth_state = build_auth_state(
            backend_base_url=args.backend_base_url,
            username=args.username,
            password=args.password,
            timeout_sec=args.timeout_sec,
        )

    results: list[RouteResult] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless)
        context = browser.new_context(viewport={"width": 1440, "height": 900})

        if auth_state:
            key_json = json.dumps(args.storage_key, ensure_ascii=False)
            payload_json = json.dumps(auth_state, ensure_ascii=False)
            context.add_init_script(
                script=(
                    "try {"
                    f"window.localStorage.setItem({key_json}, JSON.stringify({payload_json}));"
                    "} catch (e) {}"
                )
            )

        for spec in DEFAULT_ROUTES:
            started = time.perf_counter()
            notes: list[str] = []
            missing_text: list[str] = []
            status = "PASS"
            file_path = out_dir / spec.file_name
            url = f"{args.frontend_base_url.rstrip('/')}{spec.route}"

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=args.timeout_ms)
                if args.wait_ms > 0:
                    page.wait_for_timeout(args.wait_ms)

                body_text = page.inner_text("body")
                missing_text = find_missing_text(page, spec.expected_text)
                crash_hits = scan_crash_markers(body_text)

                if missing_text:
                    notes.append(f"missing expected text: {', '.join(missing_text)}")
                if crash_hits:
                    notes.append(f"detected crash markers: {', '.join(crash_hits)}")

                if missing_text or crash_hits:
                    status = "FAIL"

                page.screenshot(path=str(file_path), full_page=False)
            except Exception as exc:  # noqa: BLE001
                status = "FAIL"
                notes.append(str(exc))
                try:
                    page.screenshot(path=str(file_path), full_page=False)
                except Exception:
                    notes.append("screenshot unavailable")
            finally:
                page.close()

            duration_ms = int((time.perf_counter() - started) * 1000)
            results.append(
                RouteResult(
                    route=spec.route,
                    url=url,
                    file=str(file_path.resolve()),
                    status=status,
                    duration_ms=duration_ms,
                    expected_text=spec.expected_text,
                    missing_text=missing_text,
                    notes=notes,
                )
            )

        browser.close()

    return results, auth_state


def write_manifest(args: argparse.Namespace, out_dir: Path, results: list[RouteResult], auth_state: dict[str, Any]) -> tuple[Path, Path]:
    passed = [item for item in results if item.status == "PASS"]
    failed = [item for item in results if item.status != "PASS"]

    manifest = {
        "generatedAt": datetime.now().isoformat(),
        "frontendBaseUrl": args.frontend_base_url,
        "backendBaseUrl": args.backend_base_url,
        "authenticated": not args.no_auth,
        "authUser": auth_state.get("userName") if auth_state else None,
        "summary": {
            "total": len(results),
            "passed": len(passed),
            "failed": len(failed),
            "status": "PASS" if not failed else "FAIL",
        },
        "routes": [asdict(item) for item in results],
    }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Wave7 Frontend Usability Smoke Checklist",
        "",
        f"- Generated: `{manifest['generatedAt']}`",
        f"- Frontend: `{args.frontend_base_url}`",
        f"- Backend: `{args.backend_base_url}`",
        f"- Authenticated: `{manifest['authenticated']}`",
        "",
        "## Route Checklist",
        "",
        "| Route | Status | Screenshot | Notes |",
        "|---|---|---|---|",
    ]

    for item in results:
        note = "; ".join(item.notes) if item.notes else "-"
        screenshot = Path(item.file).name
        lines.append(f"| `{item.route}` | **{item.status}** | `{screenshot}` | {note} |")

    lines.extend(
        [
            "",
            "## Quick Summary",
            "",
            f"- Total: {len(results)}",
            f"- Passed: {len(passed)}",
            f"- Failed: {len(failed)}",
            f"- Final: {'PASS' if not failed else 'FAIL'}",
        ]
    )

    checklist_path = out_dir / "checklist.md"
    checklist_path.write_text("\n".join(lines), encoding="utf-8")

    return manifest_path, checklist_path


def main() -> int:
    args = parse_args()
    out_dir = ensure_out_dir(args.out_root, args.out_dir)

    try:
        results, auth_state = capture_routes(args, out_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] usability smoke bootstrap failed: {exc}")
        return 1

    manifest_path, checklist_path = write_manifest(args, out_dir, results, auth_state)

    failed = [item for item in results if item.status != "PASS"]
    print(json.dumps({
        "outDir": str(out_dir.resolve()),
        "manifest": str(manifest_path.resolve()),
        "checklist": str(checklist_path.resolve()),
        "failed": len(failed),
        "total": len(results),
        "status": "PASS" if not failed else "FAIL",
    }, ensure_ascii=False, indent=2))

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
