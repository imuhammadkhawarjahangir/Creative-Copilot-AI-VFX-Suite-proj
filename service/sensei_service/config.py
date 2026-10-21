import os
from pydantic import BaseModel

class Settings(BaseModel):
    # Browser origins allowed to open WebSockets (comma-separated env override).
    # Native clients such as host adapters send no Origin header and are always allowed.
    allowed_origins: list[str] = [
        o.strip()
        for o in os.environ.get("SENSEI_ALLOWED_ORIGINS", "http://127.0.0.1:5180,http://localhost:5180").split(",")
        if o.strip()
    ]

settings = Settings()
