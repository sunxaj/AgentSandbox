"""Return a ChatOpenAI instance configured for the Bonsai llama.cpp endpoint."""

from langchain_openai import ChatOpenAI
from agents.app.agent_config import get


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=get("llm", "base_url"),
        model=get("llm", "model"),
        api_key=get("llm", "api_key"),
        temperature=float(get("llm", "temperature")),
        max_tokens=int(get("llm", "max_tokens")),
    )
