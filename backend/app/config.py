"""Application configuration."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # Application
    APP_NAME: str = "Investigation Agent API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

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
    TRADE_ANALYSIS_MAX_ACCOUNTS: int = 5
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
    ENABLE_CANDIDATE_METADATA_COVERAGE: bool = False   # WI-2
    ENABLE_SAFE_SELECT_ADMISSIBILITY: bool = False     # WI-5

    class Config:
        env_file = ".env"
        case_sensitive = True


def get_settings() -> Settings:
    """Get settings instance."""
    return Settings()


settings = get_settings()
