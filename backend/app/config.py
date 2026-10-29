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
    ENABLE_GOAL_SEMANTIC_CONTRACT: bool = False   # GSP WP-GSP-2
    ENABLE_TARGET_VALIDITY_GUARD: bool = False    # GSP-7/QA
    ENABLE_CANDIDATE_METADATA_COVERAGE: bool = False   # WI-2
    ENABLE_SAFE_SELECT_ADMISSIBILITY: bool = False     # WI-5

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
