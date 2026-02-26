#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from urllib import error, request

from playwright.sync_api import sync_playwright


ROUTES = [
    ("/", "01-home.png"),
    ("/resumes", "02-resumes.png"),
    ("/rag", "03-rag.png"),
    ("/interview", "04-interview.png"),
    ("/interview/summary", "05-interview-summary.png"),
    ("/login", "06-login.png"),
]


def login(backend_base_url: str, username: str, password: str, timeout_sec: int) -> dict:
    session_id = f"mobile-shot-{int(time.time())}"
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture 6 mobile viewport screenshots")
    parser.add_argument("--frontend-base-url", default="http://127.0.0.1:3100")
    parser.add_argument("--backend-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--storage-key", default="career_hero.client_auth.v3")
    parser.add_argument("--width", type=int, default=393)
    parser.add_argument("--height", type=int, default=852)
    parser.add_argument("--wait-ms", type=int, default=1400)
    parser.add_argument("--out-root", default="screenshots")
    parser.add_argument("--out-dir", default="")
    args = parser.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.out_root) / f"mobile-app-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    auth = login(args.backend_base_url, args.username, args.password, timeout_sec=20)

    outputs: list[dict] = []
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

        for route, filename in ROUTES:
            page = context.new_page()
            url = f"{args.frontend_base_url.rstrip('/')}{route}"
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if args.wait_ms > 0:
                page.wait_for_timeout(args.wait_ms)
            file_path = out_dir / filename
            page.screenshot(path=str(file_path), full_page=False)
            outputs.append({"route": route, "file": str(file_path.resolve())})
            page.close()

        context.close()
        browser.close()

    report = {
        "generatedAt": datetime.now().isoformat(),
        "frontendBaseUrl": args.frontend_base_url,
        "viewport": {"width": args.width, "height": args.height},
        "outDir": str(out_dir.resolve()),
        "screenshots": outputs,
    }
    report_path = out_dir / "manifest.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
