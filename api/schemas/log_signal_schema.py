from pydantic import BaseModel,model_validator
from typing import Optional
from api.schemas.signal_type_schema import SignalType


class LogSignal(BaseModel):
    stage: str
    signal_type: SignalType

    fingerprint: str

    error_line: Optional[str] = None
    pre_content: Optional[str] = None
    post_content: Optional[str] = None

