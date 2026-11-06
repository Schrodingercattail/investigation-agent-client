"""Application configuration."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Application
    APP_NAME: str = "Investigation Agent API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    # Q2: GU-specific model-call configuration (restored after accidental
    # truncation; values from the approved Token-Budget experiment closure).
    # Output-token ceiling for the GOAL UNDERSTANDING model call only.
    # Default 8192 (raised from 4096 after the bounded Token-Budget
    # experiment confirmed thinking-only truncation at 4096 for the Q2
    # request shape: usage_output_tokens=4096 exactly, all failures).
    # Decision Proposer and all other LLM stages are unaffected. Must be
    # a positive integer — invalid values fail loudly at call time
    # instead of silently misconfiguring calls. Env override
    # GOAL_UNDERSTANDING_MAX_TOKENS continues to work.
    GOAL_UNDERSTANDING_MAX_TOKENS: int = 8192
    # Q2: GU-specific single-request timeout (seconds) and SDK transport
    # retry policy, applied ONLY to the Goal Understanding model call via
    # the SDK's request-scoped with_options (the shared client is never
    # mutated). Default 240s lets the observed ~190s long-thinking requests
    # complete; SDK retries are disabled for GU so a failed long request
    # fails fast instead of multiplying the wait.
    GOAL_UNDERSTANDING_TIMEOUT_SECONDS: float = 240.0
    GOAL_UNDERSTANDING_SDK_MAX_RETRIES: int = 0
    # U00008 planner reliability fix: the planner's generation budget was
    # 4096 while Goal Understanding — SAME model (glm-5.3 via the
    # Anthropic-compatible endpoint), SAME time window — ran on 8192 after
    # its own 4096→8192 fix. A simple two-finding criteria request hit
    # stop_reason=max_tokens on BOTH planner attempts at 4096 (thinking-
    # style output consumed the budget before any text block). Aligned to
    # the GU-proven value; this is the PLANNER's budget only. Env override
    # PLANNER_MAX_TOKENS continues to work.
    PLANNER_MAX_TOKENS: int = 8192

    def validate_goal_understanding_runtime(self) -> None:
        """Call-time guard (Q2 closure): invalid values fail loudly instead
        of silently misconfiguring the GU model call."""
        if not isinstance(self.GOAL_UNDERSTANDING_MAX_TOKENS, int) \
                or self.GOAL_UNDERSTANDING_MAX_TOKENS <= 0:
            raise ValueError(
                "GOAL_UNDERSTANDING_MAX_TOKENS must be a positive integer, "
                f"got {self.GOAL_UNDERSTANDING_MAX_TOKENS!r}")
        if not isinstance(self.GOAL_UNDERSTANDING_TIMEOUT_SECONDS,
                          (int, float)) \
                or self.GOAL_UNDERSTANDING_TIMEOUT_SECONDS <= 0:
            raise ValueError(
                "GOAL_UNDERSTANDING_TIMEOUT_SECONDS must be a positive "
                "number, got "
                f"{self.GOAL_UNDERSTANDING_TIMEOUT_SECONDS!r}")
        if not isinstance(self.GOAL_UNDERSTANDING_SDK_MAX_RETRIES, int) \
                or self.GOAL_UNDERSTANDING_SDK_MAX_RETRIES < 0:
            raise ValueError(
                "GOAL_UNDERSTANDING_SDK_MAX_RETRIES must be a non-negative "
                f"integer, got {self.GOAL_UNDERSTANDING_SDK_MAX_RETRIES!r}")

    # LLM Configuration
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_BASE_URL: str = ""
    ANTHROPIC_MODEL: str = "glm-5.3"

    # Risk Platform Integration
    RISK_PLATFORM_BASE_URL: str = "http://localhost:8000"

    # Cross-account trade analysis — retrieval budgets (system-controlled;
    # never LLM-chosen parameters). Provisional defaults: the VALUES are
    # not product-final decisions (docs/baselines/
    # 2026-09-18-cross-account-trade-analysis-v1.md §12); the contract is
    # that retrieval without these bounds cannot happen.
    # AL-2.10: 5 → 24. The reference case U00010's RP network attribution
    # carries 18-19 related accounts; the former budget truncated retrieval
    # (INSUFFICIENT_DATA) on every realistic request, making the governed
    # capability look broken for exactly the case it exists to serve. 24
    # still bounds retrieval (the contract is the bound, not the value).
    TRADE_ANALYSIS_MAX_ACCOUNTS: int = 24
    TRADE_ANALYSIS_MAX_TOTAL_TRANSACTIONS: int = 500
    TRADE_ANALYSIS_MAX_PAGE_SIZE: int = 100
    TRADE_ANALYSIS_MAX_PAGES: int = 10
    TRADE_ANALYSIS_MAX_CANDIDATES: int = 20
    # Matching parameters (baseline §12b.3 — DECIDED, provisional values;
    # every analysis payload echoes the values actually applied).
    TRADE_ANALYSIS_MAX_TIME_DELTA_SECONDS: float = 30.0
    TRADE_ANALYSIS_VALUE_TOLERANCE_RELATIVE: float = 0.05
    TRADE_ANALYSIS_VALUE_TOLERANCE_ABSOLUTE_FLOOR: float = 1.0
    TRADE_ANALYSIS_MAX_LEGS_PER_CANDIDATE: int = 5

    # Bounded Agent Loop (docs/baselines/2026-09-19-bounded-agent-loop-v1.md).
    # System-controlled; never prompt-embedded, never LLM-chosen. Values are
    # provisional V1 defaults (precedent: the superseded legacy loop used
    # max_steps=8).
    AGENT_LOOP_MAX_ITERATIONS: int = 8
    AGENT_LOOP_MAX_RETRIES_PER_STEP: int = 1

    # SP-M2-1: bounded goal-aware target refinement experiment
    # (docs/baselines/2026-09-21-target-goal-refinement-and-m2-rescope-v1.md).
    # When enabled, an AMBIGUOUS target resolution with retained candidates
    # runs ONE bounded goal-aware refinement attempt before clarification.
    # System-controlled; the LLM cannot bypass the confidence threshold.
    ENABLE_TARGET_GOAL_REFINEMENT: bool = False
    TARGET_REFINEMENT_MIN_CONFIDENCE: float = 0.7

    # WP-M2-1 Safe Target Refinement flags (implementation plan rev. 3).
    # Declared here as the baseline contract requires both to default OFF;
    # NO runtime behavior reads them yet — they gate later work items
    # (coverage check / admissibility gate respectively).
    # Final-acceptance calibration (2026-11-02, project-wide readiness):
    # verified, E2E-accepted product capabilities default ON; deployer-
    # choice / not-yet-E2E-verified guards stay OFF with documented
    # reasons (see the governance inventory in the acceptance reports).
    ENABLE_GOAL_SEMANTIC_CONTRACT: bool = True    # GSP WP-GSP-2 (core accepted goal/contract system; every accepted journey runs on it)
    # AL-3.5.1 G1 — Target Validity Admission Gate (S11: unevidenced
    # wrong-topic→wrong-finding binding drove finding-scoped execution and
    # a confident wrong answer; decide() admits VALID/clarifies
    # AMBIGUOUS/UNGROUNDED/INVALID). NOTE (naming): "GSP-7" was a config
    # mislabel — WP-GSP-7 is RE_PLAN/Loop Preservation (always-on, no
    # flag). Formally enabled 2026-11-03 after the isolated Flag-ON
    # verification round (legal-path compatibility: N1 safety, P1-P6, P4
    # resume, GJ-001/002/003/B1 browser — no regression; decide() covered
    # by the 41-test al351 suite). Remaining verification limits
    # (UNGROUNDED/INVALID/AMBIGUOUS real-API field evidence) are recorded
    # as known limitations in
    # docs/architecture/FEATURE_FLAG_INVENTORY.md §6 — accepted at
    # enablement, not hidden. Explicit env override still supported.
    ENABLE_TARGET_VALIDITY_GUARD: bool = True
    # WI-2 — unit + metadata_coverage_e2e_wi2 evidence, BUT default-ON
    # calibration (2026-11-02) broke the U00010-shape intake with
    # planning_failure (candidate.metadata.coverage invalidated/gone
    # cascade against non-seeded cases) — reverted to OFF; WI-2 default
    # flip is a post-acceptance verification item.
    ENABLE_CANDIDATE_METADATA_COVERAGE: bool = False
    ENABLE_SAFE_SELECT_ADMISSIBILITY: bool = True     # WI-5 — unit + safe_select_e2e_wi5/wi6 evidence; follow-up admissibility is normal product behavior
    # U00004 bounded partial completion (PMO-approved, design-validated):
    # when ON, a STOP proposal is accepted while outputs remain within an
    # honestly DISCLOSED data boundary (partially_supported + structured
    # truncation evidence, hard file_export deliverables SATISFIED, no
    # pending steps, no uncovered actionable outputs) — the run ends
    # completed / BOUNDED_DATA / goal_outcome=partial instead of forcing
    # endless re-plans (real incident TASK-14dcda628770). OFF preserves
    # the legacy coercion byte-for-byte.
    # U00004 bounded partial completion — REAL API E2E + 3-browser-journey
    # acceptance PASS (2026-11-02); normal Agent Loop termination capability.
    ENABLE_BOUNDED_PARTIAL_STOP: bool = True

    # AL-2.8: the M2 semantic foundation (Concept Registry + Candidate
    # Metadata + SemanticNormalizer) as conversational finding-resolution
    # grounding. Resolution ≠ authorization: this stage may ground a
    # finding target conversationally; it NEVER confers Safe-Select
    # eligibility (WI-5 gate stays the only authorization point). When an
    # LLM is unavailable AND no deterministic stage resolved, the turn
    # degrades to SEMANTIC_RESOLUTION_UNAVAILABLE (an honest outage state,
    # distinct from unresolved_reference). OFF = legacy resolution chain
    # without the M2 stage.
    ENABLE_SEMANTIC_FINDING_RESOLUTION: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = True


def get_settings() -> Settings:
    """Get settings instance."""
    return Settings()


settings = get_settings()
