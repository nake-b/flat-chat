from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic_ai.messages import ModelMessage
from sqlalchemy.orm import Session

from flat_chat.chat.schemas import ConversationResponse, MessageCreate, MessageResponse
from flat_chat.chat.service import ChatService
from flat_chat.core.database import get_db

router = APIRouter()

conversations: dict[str, list[ModelMessage]] = {}


@router.post("", response_model=ConversationResponse)
def create_conversation():
    conv_id = str(uuid4())
    conversations[conv_id] = []
    return ConversationResponse(id=conv_id, created_at=datetime.now(UTC))


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(
    conversation_id: str,
    body: MessageCreate,
    db: Session = Depends(get_db),
):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    history = conversations[conversation_id]

    svc = ChatService(db)
    result = await svc.send_message(body.content, history)

    conversations[conversation_id].extend(result.new_messages)

    return MessageResponse(
        id=str(uuid4()),
        role="assistant",
        content=result.output,
        created_at=datetime.now(UTC),
    )


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def get_messages(conversation_id: str):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    result = []
    for msg in conversations[conversation_id]:
        for part in msg.parts:
            if hasattr(part, "content") and isinstance(part.content, str):
                role = "user" if msg.kind == "request" else "assistant"
                result.append(
                    MessageResponse(
                        id=str(uuid4()),
                        role=role,
                        content=part.content,
                        created_at=datetime.now(UTC),
                    )
                )
    return result
