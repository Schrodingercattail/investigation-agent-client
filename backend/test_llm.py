from app.llm_provider import ClaudeProvider


provider = ClaudeProvider()

response = provider.generate(
    [
        {
            "role": "user",
            "content": "Reply with exactly: TOOL_PROVIDER_OK",
        }
    ]
)

print(response)