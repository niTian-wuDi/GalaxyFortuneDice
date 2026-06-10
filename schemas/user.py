from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class UserBase(BaseModel):
    phone: Optional[str] = None
    nickname: Optional[str] = None

class UserCreate(UserBase):
    password: Optional[str] = None

class UserLogin(BaseModel):
    phone: str
    password: str

class UserResponse(UserBase):
    id: int
    avatar: str
    exp: int
    create_time: datetime

    model_config = {"from_attributes": True}

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class AuthResponse(BaseModel):
    token: str

class UserInfoResponse(BaseModel):
    """用户详细信息，包含历史统计"""
    id: int
    phone: Optional[str] = None
    nickname: str
    avatar: str
    exp: int
    create_time: datetime
    total_games: int = 0
    total_wins: int = 0
    max_score: int = 0

    model_config = {"from_attributes": True}


