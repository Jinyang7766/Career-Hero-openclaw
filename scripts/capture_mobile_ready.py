#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

ROUTES = [
    ("/", "01-home.png", "输入区"),
    ("/resumes", "02-resumes.png", "简历库"),
    ("/rag", "03-rag.png", "知识"),
    ("/interview", "04-interview.png", "面试练习"),
    ("/interview/summary", "05-interview-summary.png", "面试总结"),
    ("/login", "06-login.png", "账号登录"),
]


def login(backend_base_url: str, username: str, password: str, timeout_sec: int) -> dict:
    session_id = f"mobile-ready-{int(time.time())}"
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = request.Request(
        f"{backend_base_url.rstrip('/')}/api/auth/login",
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "x-session-id": session_id,
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"login failed {exc.code}: {body}") from exc

    return {
        "sessionId": data.get("sessionId", session_id),
        "accessToken": data.get("token", ""),
        "refreshToken": None,
        "mode": "custom",
        "updatedAt": datetime.utcnow().isoformat() + "Z",
        "userName": (data.get("user") or {}).get("username", username),
        "expiresAt": data.get("expiresAt"),
    }


def wait_ready(page, expected_text: str, timeout_ms: int) -> None:
    page.wait_for_selector(".app-shell__container", timeout=timeout_ms)
    page.wait_for_function(
        "() => !document.body.innerText.includes('页面加载中')",
        timeout=timeout_ms,
    )
    page.get_by_text(expected_text, exact=False).first.wait_for(timeout=timeout_ms)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture mobile screenshots after page is fully ready")
    parser.add_argument("--frontend-base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--storage-key", default="career_hero.client_auth.v3")
    parser.add_argument("--width", type=int, default=393)
    parser.add_argument("--height", type=int, default=852)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--out-root", default="C:/Users/sunshine/.openclaw/workspace/shots-wave10-mobile-ready")
    args = parser.parse_args()

    out_dir = Path(args.out_root) / f"mobile-ready-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    auth = login(args.backend_base_url, args.username, args.password, timeout_sec=20)

    outputs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": args.width, "height": args.height}, device_scale_factor=3)
        context.add_init_script(
            script=(
                "try {"
                f"window.localStorage.setItem({json.dumps(args.storage_key)}, JSON.stringify({json.dumps(auth, ensure_ascii=False)}));"
                "} catch (e) {}"
            )
        )

        for route, filename, expected in ROUTES:
            url = f"{args.frontend_base_url.rstrip('/')}{route}"
            page = context.new_page()
            note = "ok"
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                wait_ready(page, expected, args.timeout_ms)
            except PWTimeout:
                note = "timeout_wait_ready"
            except Exception as exc:  # noqa: BLE001
                note = f"error:{exc}"
            file_path = out_dir / filename
            page.screenshot(path=str(file_path), full_page=False)
            outputs.append({"route": route, "file": str(file_path.resolve()), "expected": expected, "note": note})
            page.close()

        context.close()
        browser.close()

    manifest = {
        "generatedAt": datetime.now().isoformat(),
        "outDir": str(out_dir.resolve()),
        "viewport": {"width": args.width, "height": args.height},
        "screenshots": outputs,
    }
    mf = out_dir / "manifest.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
