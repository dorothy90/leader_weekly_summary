from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field


class PolicyContext(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    user_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.@+-]+$",
    )
    decision_id: str = Field(min_length=32, max_length=32)

    @classmethod
    def from_user_id(cls, user_id: str) -> "PolicyContext":
        normalized = user_id.strip()
        decision = sha256(f"owner:{normalized}".encode()).hexdigest()[:32]
        return cls(user_id=normalized, decision_id=decision)
