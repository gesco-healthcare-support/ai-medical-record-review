"""Request bodies for the /api/admin router.

Category id is a numeric STRING and immutable, so CategoryUpdate does not accept it. Business
checks (numeric id, duplicate, empty-after-strip name) stay in the route as 400s (matching the
Flask contract); Pydantic enforces the structural types.
"""

from typing import Any

from pydantic import BaseModel


class CategoryCreate(BaseModel):
    id: str
    name: str
    description: str = ""
    examples: list[Any] = []
    active: bool = True
    auto_assign: bool = True


class CategoryUpdate(BaseModel):
    # id intentionally omitted (immutable). All optional; the route applies only fields the client
    # actually sent (model_dump(exclude_unset=True)).
    name: str | None = None
    description: str | None = None
    examples: list[Any] | None = None
    active: bool | None = None
    auto_assign: bool | None = None


class PromptPut(BaseModel):
    text: str = ""
