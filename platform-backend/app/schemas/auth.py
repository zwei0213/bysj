from typing import Literal
from pydantic import BaseModel, Field


class LoginRequestDTO(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class RegisterRequestDTO(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(default="", max_length=120)
    password: str = Field(..., min_length=8, max_length=128)
    role: Literal["student", "teacher"] = "student"


class RefreshTokenRequestDTO(BaseModel):
    refresh_token: str = Field(..., min_length=10)


class PasswordChangeDTO(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class SupportTicketCreateDTO(BaseModel):
    category: str = "课程与平台反馈"
    subject: str = Field(..., min_length=2, max_length=160)
    description: str = ""


class NotificationPatchDTO(BaseModel):
    is_read: bool = True
