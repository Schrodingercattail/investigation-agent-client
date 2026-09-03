"""Browser smoke test: POST /api/v2/investigations + first turn for the
three reported inputs — "00299", "investigate case 00299", "investigate
U00299" — all must resolve to canonical U00299 without HTTP 500.

Run standalone (no pytest): .venv/bin/python tests/test_smoke_case_resolution.py
"""

import json
import sys
import tempfile

from fastapi.testclient import TestClient


def build():
    from app.api import investigations as inv_api
    from app.api.investigations import InvestigationAPI
    from app.investigation_service import InvestigationService
    from app.task_store_v2 import TaskStoreV2

    store = TaskStoreV2(tempfile.mkdtemp() + "/smoke.db")
    api = inv_api.InvestigationAPI(
        service=InvestigationService(task_store=store),
        store=store,
        case_findings_provider=lambda case_id: [],
    )
    inv_api.api = api
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def main() -> int:
    client = build()
    failures = []

    for raw in ("00299", "investigate case 00299", "investigate U00299"):
        r = client.post("/api/v2/investigations", json={"message": raw})
        if r.status_code != 200:
            failures.append(f"{raw!r}: create returned {r.status_code}")
            continue
        body = r.json()
        inv_id = body["investigation"]["investigation_id"]
        case_id = body["investigation"]["case_id"]
        if case_id != "U00299":
            failures.append(f"{raw!r}: resolved to {case_id}, want U00299")
            continue
        print(f"OK  {raw!r:32} -> investigation {inv_id}, case {case_id}")

        # first turn must never be a 500 (RP-down yields a bounded failure)
        turn = client.post(f"/api/v2/investigations/{inv_id}/turn",
                           json={"message": raw})
        if turn.status_code >= 500:
            failures.append(f"{raw!r}: turn returned {turn.status_code}")
            continue
        tb = turn.json()
        print(f"    turn HTTP {turn.status_code}, status={tb['status']}, "
              f"task={tb['task']['status']}")

    if failures:
        print("\nSMOKE FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nSMOKE PASSED: all three inputs resolve to U00299, no HTTP 500")
    return 0


if __name__ == "__main__":
    sys.exit(main())
