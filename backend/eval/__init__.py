"""Week 1 Agent Evaluation — semantic evaluation of the Investigation Agent.

Evaluates the Agent itself (intent, targeting, skill/tool selection,
evidence-stream scope, multi-turn consistency, bounded honesty, overall
semantic task success). Does NOT evaluate Risk Platform LLM explanation
quality — that is owned by the Risk Platform.

Key principle: task.status completion is NOT semantic success. Scenarios
assert semantic expectations (planned steps, tool/argument/ stream/target
correctness, outcome class, response meaning, scope containment) and pass
or fail on those — independent of whether the task "completed".
"""
