from app.agent import InvestigationAgent


agent = InvestigationAgent()

result = agent.run(
    user_intent="Investigate case U00299",
    max_steps=8,
)

print("\n=== FULL AGENT RUN ===")
print(f"Status: {result['status']}")

for step in result.get("steps", []):
    print(f"\nStep {step['step']}")
    print(f"Tool: {step['tool_name']}")
    print(f"Args: {step['tool_args']}")
    print(f"Result keys: {list(step['result'].keys())}")

print("\n=== FINAL DECISION ===")
print(result.get("final_decision"))

if result.get("error"):
    print("\n=== ERROR ===")
    print(result["error"])