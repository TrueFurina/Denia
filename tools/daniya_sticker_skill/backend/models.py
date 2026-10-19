"""API 请求/响应模型（pydantic，随 fastapi 自带）。"""
from typing import List, Optional
from pydantic import BaseModel


class PackCreate(BaseModel):
    name: str


class PackRename(BaseModel):
    name: str


class BindingsUpdate(BaseModel):
    mode: str  # none | whitelist | blacklist
    characters: List[str] = []


class DescribeRequest(BaseModel):
    pack_id: str
    meme_id: str


class SearchRequest(BaseModel):
    query: str
    pack_id: Optional[str] = None
    character: Optional[str] = None
    top_k: int = 10


class PickRequest(BaseModel):
    character: str
    conversation: List[dict]  # [{"role": "user"|"assistant", "content": "..."}]
    pack_id: Optional[str] = None
