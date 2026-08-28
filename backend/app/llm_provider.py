import logging
import os

from anthropic import Anthropic
from anthropic import APITimeoutError
from anthropic import RateLimitError as AnthropicRateLimitError
from anthropic import APIConnectionError, APIStatusError
from dotenv import load_dotenv

from app.exceptions import (
    LLMConfigurationError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)


load_dotenv()


logger = logging.getLogger(__name__)


class ClaudeProvider:
    """Provider for Claude LLM with robust error handling."""

    DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
    DEFAULT_MAX_TOKENS = 4096
    DEFAULT_TIMEOUT = 60.0

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        """
        Initialize Claude provider.

        Args:
            api_key: Anthropic API key (defaults to ANTHROPIC_API_KEY env var)
            base_url: Optional base URL override (defaults to ANTHROPIC_BASE_URL env var)
            model: Model name (defaults to ANTHROPIC_MODEL env var or DEFAULT_MODEL)

        Raises:
            LLMConfigurationError: If configuration is invalid
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL")
        self.model = model or os.getenv("ANTHROPIC_MODEL") or self.DEFAULT_MODEL

        if not self.api_key:
            raise LLMConfigurationError(
                "ANTHROPIC_API_KEY is not configured",
                context={"env_var": "ANTHROPIC_API_KEY"},
            )

        if not self.model:
            raise LLMConfigurationError(
                "ANTHROPIC_MODEL is not configured",
                context={"env_var": "ANTHROPIC_MODEL"},
            )

        try:
            self.client = Anthropic(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.DEFAULT_TIMEOUT,
            )
            logger.info(f"ClaudeProvider initialized with model: {self.model}")
        except Exception as e:
            raise LLMConfigurationError(
                f"Failed to initialize Anthropic client: {str(e)}",
                context={"base_url": self.base_url},
            ) from e

    def generate(
        self,
        messages: list[dict],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = 0.1,
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens in response
            temperature: Sampling temperature (0.0 to 1.0)

        Returns:
            The generated text response

        Raises:
            LLMTimeoutError: If the request times out
            LLMRateLimitError: If rate limit is exceeded
            LLMError: For other LLM-related errors
            LLMConfigurationError: If configuration is invalid
        """
        try:
            logger.debug(f"Sending {len(messages)} messages to LLM (model: {self.model})")
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
            )

            for block in response.content:
                if getattr(block, "type", None) == "text":
                    logger.debug(f"Received LLM response: {len(block.text)} chars")
                    return block.text

            raise LLMError(
                "LLM response contained no text block",
                context={"response_type": str(type(response.content))},
            )

        except APITimeoutError as e:
            logger.error(f"LLM request timed out: {str(e)}")
            raise LLMTimeoutError(
                f"LLM request timed out after {self.DEFAULT_TIMEOUT}s",
                context={"timeout": self.DEFAULT_TIMEOUT},
            ) from e

        except AnthropicRateLimitError as e:
            logger.error(f"LLM rate limit exceeded: {str(e)}")
            raise LLMRateLimitError(
                "LLM rate limit exceeded - please retry after a delay",
                context={"retry_after": getattr(e, "retry_after", None)},
            ) from e

        except APIStatusError as e:
            status_code = e.status_code
            logger.error(f"LLM API returned error status {status_code}: {str(e)}")

            if status_code == 401:
                raise LLMConfigurationError(
                    "LLM authentication failed - check API key",
                    context={"status_code": status_code},
                ) from e
            elif status_code == 400:
                raise LLMConfigurationError(
                    f"LLM request validation failed: {str(e)}",
                    context={"status_code": status_code, "error": str(e)},
                ) from e
            else:
                raise LLMError(
                    f"LLM API error: {str(e)}",
                    context={"status_code": status_code},
                ) from e

        except APIConnectionError as e:
            logger.error(f"LLM connection error: {str(e)}")
            raise LLMError(
                f"Failed to connect to LLM API: {str(e)}",
                context={"base_url": self.base_url},
            ) from e

        except Exception as e:
            logger.error(f"Unexpected LLM error: {str(e)}", exc_info=True)
            raise LLMError(
                f"Unexpected LLM error: {str(e)}",
                context={"error_type": type(e).__name__},
            ) from e