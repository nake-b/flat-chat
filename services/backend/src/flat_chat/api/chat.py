from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from flat_chat.chat.schemas import ConversationResponse, MessageCreate, MessageResponse
from flat_chat.chat.service import ChatService

router = APIRouter()

conversations: dict[str, list[dict]] = {}


@router.post("", response_model=ConversationResponse)
def create_conversation():
    conv_id = str(uuid4())
    conversations[conv_id] = []
    return ConversationResponse(id=conv_id, created_at=datetime.now(UTC))


@router.post("/{conversation_id}/messages", response_model=MessageResponse)
async def send_message(conversation_id: str, body: MessageCreate):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    history = conversations[conversation_id]

    svc = ChatService()
    assistant_content = await svc.send_message(body.content, history)

    user_msg = {
        "id": str(uuid4()),
        "role": "user",
        "content": body.content,
        "created_at": datetime.now(UTC),
    }
    assistant_msg = {
        "id": str(uuid4()),
        "role": "assistant",
        "content": assistant_content,
        "created_at": datetime.now(UTC),
    }
    conversations[conversation_id].extend([user_msg, assistant_msg])

    return MessageResponse(**assistant_msg)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def get_messages(conversation_id: str):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [MessageResponse(**m) for m in conversations[conversation_id]]
