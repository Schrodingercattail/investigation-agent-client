from app.models import Step, StepStatus


def create_investigation_plan() -> list[Step]:
    return [
        Step(
            id="step_policy_search",
            name="Policy Retrieve",
            description="Retrieve relevant policy snippets using RAG.",
            status=StepStatus.PENDING,
            tool_name="policy_search",
        ),
        Step(
            id="step_evidence_fetch",
            name="Evidence Fetch",
            description="Fetch canonical evidence and unified findings for the case.",
            status=StepStatus.PENDING,
            tool_name="evidence_fetch",
        ),
        Step(
            id="step_compose",
            name="Draft Findings & Actions",
            description="Generate structured findings and actions grounded in evidence.",
            status=StepStatus.PENDING,
            tool_name="compose_structured_result",
        ),
        Step(
            id="step_citation_validate",
            name="Citation Validation",
            description="Validate claim-level citations and trigger fallback when necessary.",
            status=StepStatus.PENDING,
            tool_name="citation_validate",
        ),
    ]