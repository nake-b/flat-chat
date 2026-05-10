import litellm

from flat_chat.core.config import settings

SYSTEM_PROMPT = (
    "You are a helpful Berlin apartment search assistant. "
    "You help users find apartments in Berlin by asking about their preferences "
    "(budget, neighborhood, size, move-in date, furnished/unfurnished, etc.) "
    "and providing relevant advice about Berlin's rental market. "
    "Be concise, friendly, and practical. "
    "If users ask about things unrelated to apartment searching in Berlin, "
    "gently steer them back to the topic."
)


async def get_completion(messages: list[dict[str, str]]) -> str:
    llm_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    llm_messages.extend(messages)

    response = await litellm.acompletion(
        model=settings.llm_model,
        messages=llm_messages,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        api_key=settings.llm_api_key or None,
        num_retries=settings.llm_num_retries,
        retry_after=settings.llm_retry_after,
    )

    return response.choices[0].message.content
