import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()
uvicorn.run(
    "server.entrypoints:users",
    factory=True,
    host=os.environ.get("USER_HOST", "127.0.0.1"),
    port=int(os.environ.get("USER_PORT", "3002")),
    workers=1,
    access_log=False,
)
