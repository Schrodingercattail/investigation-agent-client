from app.agent import InvestigationAgent


agent = InvestigationAgent()

result = agent.run_once(
    user_intent="Investigate case U00299"
)

print("\n=== AGENT RESULT ===")
print(result)