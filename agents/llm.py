"""
Shared LLM client — Groq is free with generous rate limits.
All agents import from here so the model is configured once.

Free Groq models (as of 2024):
  - llama-3.3-70b-versatile   (best quality, 32k context)
  - llama-3.1-8b-instant       (fastest)
  - mixtral-8x7b-32768         (large context window)
"""
from langchain_groq import ChatGroq
from config import settings

_llm_instance: ChatGroq | None = None


def get_llm(temperature: float = 0.0) -> ChatGroq:
    """Return a cached ChatGroq instance configured from settings."""
    global _llm_instance
    if _llm_instance is None or _llm_instance.temperature != temperature:
        _llm_instance = ChatGroq(
            model=settings.llm_model,
            temperature=temperature,
            api_key=settings.groq_api_key,
            max_retries=settings.agent_max_retries,
        )
    return _llm_instance
