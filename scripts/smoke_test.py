#!/usr/bin/env python3
"""Smoke test: boots the API and asserts the production-bar endpoints.

Usage:
    python scripts/smoke_test.py

Exits non-zero on the first failure. Uses dummy placeholder keys only —
no real credentials are needed or used.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

API_KEY = "smoke-test-secret-do-not-use"
PORT = 18088

ENV = {
    **os.environ,
    "API_KEY": API_KEY,
    "DATABASE_URL": "sqlite+aiosqlite:///" + os.path.join(tempfile.gettempdir(), "smoke_social.db"),
    "OPENAI_API_KEY": "sk-dummy-placeholder",
    "PORT": str(PORT),
}


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass
        return e.code, payload


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        sys.exit(1)


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = ENV["DATABASE_URL"].split("///")[-1]
    if os.path.exists(db_path):
        os.remove(db_path)

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.server:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=repo_root,
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for boot
        for _ in range(60):
            try:
                code, _ = call("GET", "/health")
                if code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            check("server boots", False, "never became ready")

        code, body = call("GET", "/health")
        check("GET /health -> 200", code == 200 and body.get("status") == "ok", f"{code} {body}")

        code, body = call("POST", "/api/execute", {"brand_voice": "x" * 20, "post_count": 5, "model_id": "gpt-4o"})
        check("POST /api/execute without key -> 401", code == 401, f"{code} {body}")

        code, body = call(
            "POST", "/api/execute",
            {"brand_voice": "x" * 20, "post_count": 5, "model_id": "gpt-4o"},
            {"X-API-Key": "wrong"},
        )
        check("POST /api/execute with wrong key -> 401", code == 401, f"{code} {body}")

        code, body = call(
            "POST", "/api/execute",
            {"brand_voice": "short", "post_count": 500, "model_id": "evil-model"},
            {"X-API-Key": API_KEY},
        )
        check("POST /api/execute invalid input -> 422", code == 422 and "error" in body, f"{code} {body}")

        code, body = call(
            "POST", "/api/execute",
            {"brand_voice": "Friendly cafe brand posting daily specials", "post_count": 3, "model_id": "ollama/llama3"},
            {"X-API-Key": API_KEY},
        )
        check("POST /api/execute valid -> 202 with task_id", code == 202 and body.get("task_id"), f"{code} {body}")
        task_id = body["task_id"]

        time.sleep(2)  # let the (doomed, no Ollama) background job run
        code, body = call("GET", f"/api/tasks/{task_id}", headers={"X-API-Key": API_KEY})
        check("GET /api/tasks/<id> -> 200", code == 200 and body.get("status") in ("pending", "running", "error", "success"), f"{code} {body}")

        code, body = call("GET", "/api/tasks/not-a-uuid", headers={"X-API-Key": API_KEY})
        check("GET /api/tasks/bad-id -> 400", code == 400, f"{code} {body}")

        code, body = call("GET", "/api/tasks/00000000-0000-0000-0000-000000000000", headers={"X-API-Key": API_KEY})
        check("GET /api/tasks/unknown -> 404", code == 404, f"{code} {body}")

        code, body = call("GET", f"/api/tasks/{task_id}")
        check("GET /api/tasks/<id> without key -> 401", code == 401, f"{code} {body}")

        code, body = call("POST", "/api/settings/keys", {"openai_api_key": "sk-dummy"}, {"X-API-Key": API_KEY})
        check("POST /api/settings/keys with key -> 200", code == 200 and body.get("status") == "success", f"{code} {body}")

        code, body = call("POST", "/api/settings/keys", {"openai_api_key": "sk-dummy"})
        check("POST /api/settings/keys without key -> 401", code == 401, f"{code} {body}")

        print("\nAll smoke tests passed.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
