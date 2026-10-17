"""Provider selection stays local by default; optional dependencies load on demand."""
from llm_client import LLMClient


def make_client(cfg):
    provider = cfg.get("provider", "player2")
    if provider == "chatgpt_plan":
        from chatgpt_client import ChatGPTPlanClient
        return ChatGPTPlanClient(cfg)
    if provider in ("player2", "local", "openai_compatible"):
        return LLMClient(cfg)
    raise ValueError("Unknown provider: " + str(provider))


def configured_client(cfg):
    client = make_client(cfg)
    if cfg.get("unified") or cfg.get("usage", {}).get("enabled", False):
        from usage_budget import BudgetClient
        return BudgetClient(client, cfg)
    return client
