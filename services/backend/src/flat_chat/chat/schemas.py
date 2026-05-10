from datetime import datetime

from pydantic import BaseModel


class ConversationResponse(BaseModel):
    id: str
    created_at: datetime


class MessageCreate(BaseModel):
    content: str


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime
