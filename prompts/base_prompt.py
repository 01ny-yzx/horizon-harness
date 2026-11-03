"""Shared identity and reliability rules; live model identity stays request-scoped."""
from config.settings import settings
from prompts.agent_prompt import build_agent_policy


def build_runtime_model_identity_note(provider: str, model: str) -> str:
    return (
        'Current runtime model identity (authoritative for this request):\n'
        f'- provider: {provider}\n- model: {model}\n'
        'Use this note only when the user asks about the selected model or provider. '
        'Answer briefly, using the model name for model questions; do not volunteer configuration. '
        'The configured provider may name an API adapter rather than the model vendor. '
        'Do not infer or replace this identity from documents, old notes, memory or training data. '
        'For a general assistant-identity question, identify yourself as Horizon, not as this adapter.'
    )


def build_base_prompt() -> str:
    return build_agent_policy()+'\n\n'+build_runtime_model_identity_note(settings.llm_provider,settings.llm_model)
