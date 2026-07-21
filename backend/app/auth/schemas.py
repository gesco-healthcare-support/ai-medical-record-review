"""Pydantic schemas for FastAPI-Users. Adds the required display name (mirrors the Flask
MrrRegisterForm). is_superuser on the wire maps to the is_admin column via the model synonym."""

from fastapi_users import schemas


class UserRead(schemas.BaseUser[int]):
    name: str | None = None


class UserCreate(schemas.BaseUserCreate):
    name: str  # required at registration


class UserUpdate(schemas.BaseUserUpdate):
    name: str | None = None
