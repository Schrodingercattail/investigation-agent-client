"""WP-GS-D L3 (partial-identity supplement) — backend launcher with a
MARKED data-injection boundary.

Canonical topology :3001 → :8001 → :8000. The ONLY non-production
element is the ADAPTER data source: `RiskPlatformAdapter.
fetch_case_evidence` serves a deterministic fixture payload whose
withdrawal stream holds 9 records — 6 with recorded destination
identifiers (two groups) and 3 without. This is the SAME injection
surface and the SAME fixture the GS-D API E2E suite uses
(`tests/test_cg_gs_d_api_e2e_v1.py: PARTIAL_ROWS/_withdrawal_fixture`);
it is the point where test data enters, exactly as allowed for this
acceptance supplement.

Everything downstream is the REAL production chain, unmodified:
finding_drilldown (stream=withdrawals) → `_destination_identity_block`
(GS-B facts) → `_destination_identity_summary` observation projection →
GSP-3 association → GSP-4 facet evaluation → GSP-5 GoalOutcome
derivation → composer → Activity projection → the browser page.

The findings provider (QA-04) and case intake remain LIVE Risk
Platform calls; the Risk Platform server itself is untouched.
"""
import sys

sys.path.insert(0, "/Users/vv/investigation-agent-client/backend")

from unittest.mock import patch                    # noqa: E402

from app.adapters.risk_platform import RiskPlatformAdapter  # noqa: E402
from tests.test_cg_gs_d_api_e2e_v1 import (        # noqa: E402
    PARTIAL_ROWS, _withdrawal_fixture)

FIXTURE = _withdrawal_fixture(PARTIAL_ROWS)


def _serve_fixture(self, uid, **kw):
    return FIXTURE


def main():
    patcher = patch.object(RiskPlatformAdapter,
                           "fetch_case_evidence", _serve_fixture)
    patcher.start()
    import uvicorn
    from app.main import app
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")


if __name__ == "__main__":
    main()
