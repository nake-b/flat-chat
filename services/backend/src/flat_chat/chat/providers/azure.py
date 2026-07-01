"""Azure OpenAI provider.

Pydantic AI's AzureProvider handles the api-version + deployment-name URL
shape that classic Azure OpenAI Service requires. We pass the deployment
name (not the underlying model name) as the model identifier — Azure routes
the request through the deployment.
"""

import logging

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider

from flat_chat.core.config import Settings

logger = logging.getLogger(__name__)


def build_azure_model(settings: Settings, deployment: str) -> Model:
    """Build an Azure OpenAI model routed through `deployment`.

    The caller passes the deployment to use — the chat deployment for chat, or
    the title deployment (with its own fallback) for titling. Owns its own
    validation: the orchestrator only checks for key presence, so this builder
    raises with a clear message when the rest of the Azure config is incomplete.
    """
    missing = [
        name
        for name in (
            "azure_openai_endpoint",
            "azure_openai_deployment",
            "azure_openai_api_version",
        )
        if not getattr(settings, name)
    ]
    if missing:
        raise RuntimeError(
            "AZURE_OPENAI_API_KEY is set but the following are empty: "
            + ", ".join(name.upper() for name in missing)
            + ". Set them in .env."
        )
    return OpenAIChatModel(
        deployment,
        provider=AzureProvider(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        ),
    )
