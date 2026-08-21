import os

from anthropic import Anthropic
from dotenv import load_dotenv


load_dotenv()


class ClaudeProvider:
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        base_url = os.getenv("ANTHROPIC_BASE_URL")
        model = os.getenv("ANTHROPIC_MODEL")

        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured")

        if not model:
            raise ValueError("ANTHROPIC_MODEL is not configured")

        self.client = Anthropic(
            api_key=api_key,
            base_url=base_url,
        )

        self.model = model

    def generate(self, messages: list[dict]) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            temperature=0.1,
            messages=messages,
        )

        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text

        raise ValueError("LLM response contained no text block")