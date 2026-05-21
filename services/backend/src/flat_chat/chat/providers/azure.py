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


def build_azure_model(settings: Settings) -> Model:
    return OpenAIChatModel(
        settings.azure_openai_deployment,
        provider=AzureProvider(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        ),
    )
