from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.schemas import ConversationResponse, MessageCreate, MessageResponse

router = APIRouter()

conversations: dict[str, list[dict]] = {}


@router.post("", response_model=ConversationResponse)
def create_conversation():
    conv_id = str(uuid4())
    conversations[conv_id] = []
    return ConversationResponse(id=conv_id, created_at=datetime.now(UTC))


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
def send_message(conversation_id: str, body: MessageCreate):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    user_msg = {
        "id": str(uuid4()),
        "role": "user",
        "content": body.content,
        "created_at": datetime.now(UTC),
    }
    conversations[conversation_id].append(user_msg)

    assistant_msg = {
        "id": str(uuid4()),
        "role": "assistant",
        "content": f"I heard you say: '{body.content}'. I'm a dummy bot for now!",
        "created_at": datetime.now(UTC),
    }
    conversations[conversation_id].append(assistant_msg)

    return MessageResponse(**assistant_msg)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def get_messages(conversation_id: str):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [MessageResponse(**m) for m in conversations[conversation_id]]
