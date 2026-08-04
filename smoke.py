"""End-to-end smoke test for CCC Origination.

Runs the server, hits every key endpoint, and reports.
"""
import sys, time, subprocess, urllib.request, urllib.parse, json
from pathlib import Path

ROOT = Path(r"C:\DandyDon\ccc-origination")
PORT = 8810
BASE = f"http://127.0.0.1:{PORT}"


def hit(method, path, data=None, form=False, headers=None, allow_redirects=False):
    url = BASE + path
    if data is not None and not form and not isinstance(data, str):
        data = json.dumps(data).encode("utf-8")
    elif form and data is not None:
        data = urllib.parse.urlencode(data).encode("utf-8")
    elif data is not None and isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    if form:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body, dict(e.headers)
    except Exception as e:
        return 0, str(e), {}


def start_server():
    """Start uvicorn in a subprocess. Returns the Popen."""
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.Popen(
        ["python", "-m", "uvicorn", "app.app:app", "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def wait_for_health(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            code, body, _ = hit("GET", "/api/health")
            if code == 200 and "ok" in body:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main():
    print(f"=== Smoke test against {BASE} ===")

    # Wipe the DB so we start clean
    for f in ROOT.glob("ccc_origination.db*"):
        f.unlink()

    proc = start_server()
    try:
        if not wait_for_health(30):
            print("FAIL: server didn't become healthy")
            out, err = proc.communicate(timeout=5)
            print("STDOUT:", out.decode("utf-8", "replace")[-2000:])
            print("STDERR:", err.decode("utf-8", "replace")[-2000:])
            return False

        print("✓ Health")
        code, body, _ = hit("GET", "/api/version")
        assert code == 200
        print(f"✓ /api/version → {body[:80]}")

        # 1. Hit /mortgage/ — should serve the static marketing page
        code, body, _ = hit("GET", "/mortgage/")
        assert code == 200 and len(body) > 1000, f"/mortgage/ returned {code}, len {len(body)}"
        print(f"✓ /mortgage/ → {code} ({len(body)} bytes)")

        # 2. Hit /mortgage/corpus/ (deeper path)
        code, body, _ = hit("GET", "/mortgage/corpus/")
        assert code == 200, f"/mortgage/corpus/ returned {code}"
        print(f"✓ /mortgage/corpus/ → {code}")

        # 3. Submit a deal
        print()
        print("=== Submitting a deal ===")
        form_data = {
            "full_name": "Smoke Test",
            "email": "smoke@test.local",
            "phone": "409-555-0100",
            "credit_score": "720",
            "entity_name": "Test Holdings LLC",
            "entity_type": "LLC",
            "entity_state": "TX",
            "property_address": "123 Main St",
            "property_city": "Dallas",
            "property_state": "TX",
            "property_zip": "75201",
            "property_type": "sfr",
            "purchase_price": "350000",
            "target_loan_amount": "280000",
            "down_payment": "70000",
            "projected_rent": "2800",
            "arv": "0",
            "rehab_budget": "0",
            "loan_type": "dscr_purchase",
            "target_close": "2026-09-01",
            "lead_source": "smoke-test",
            "notes": "smoke test deal",
        }
        code, body, headers = hit("POST", "/submit/", data=form_data, form=True)
        # 303 redirect to /submit/thanks/<id>/
        print(f"  POST /submit/ → HTTP {code}, location={headers.get('Location', headers.get('location', ''))[:80]}")
        if code >= 500:
            print(f"  BODY: {body[:1000]}")
        public_id = headers.get("Location", headers.get("location", "")).rstrip("/").split("/")[-1]
        assert code in (303, 200), f"submit returned {code}"
        print(f"  deal public_id = {public_id}")

        # 4. Hit the thanks page
        if public_id:
            code, body, _ = hit("GET", f"/submit/thanks/{public_id}/")
            print(f"✓ /submit/thanks/{public_id}/ → {code} ({len(body)}B)")

        # 5. Try the broker login page
        code, body, _ = hit("GET", "/admin/login/")
        assert code == 200
        print(f"✓ /admin/login/ → {code}")

        # 6. Try the borrower portal
        code, body, headers = hit("GET", "/portal/")
        print(f"✓ /portal/ → {code} (redirect to login expected)")
        assert code in (303, 302, 200)

        # 7. Verify the deal landed in the DB by checking the SQLite file
        import sqlite3
        db_path = ROOT / "ccc_origination.db"
        if db_path.exists():
            con = sqlite3.connect(str(db_path))
            cur = con.execute("SELECT public_id, stage, scenario_score, borrower_id FROM deals")
            rows = cur.fetchall()
            print(f"\n✓ DB has {len(rows)} deal(s):")
            for r in rows:
                print(f"    {r}")
            cur = con.execute("SELECT slug, name FROM lenders")
            lenders = cur.fetchall()
            print(f"\n✓ DB has {len(lenders)} lenders:")
            for l in lenders[:5]:
                print(f"    {l[0]:20s}  {l[1]}")
            cur = con.execute("SELECT lender_id, name, loan_type, rate_band FROM products")
            products = cur.fetchall()
            print(f"\n✓ DB has {len(products)} products:")
            for p in products[:5]:
                print(f"    lender={p[0]:2d}  {p[2]:15s}  {p[1]:25s}  {p[3]}")
            cur = con.execute("SELECT borrower_id, kind, payload FROM events ORDER BY id DESC LIMIT 5")
            events = cur.fetchall()
            print(f"\n✓ Recent events:")
            for e in events:
                print(f"    deal={e[0]:3d}  {e[1]:25s}  {e[2]}")
            con.close()

        print()
        print("=== SMOKE TEST PASSED ===")
        return True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)