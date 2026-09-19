import argparse
import os

import uvicorn
from dotenv import load_dotenv

parser = argparse.ArgumentParser()
parser.add_argument("--reload", action="store_true")
args = parser.parse_args()
load_dotenv()
uvicorn.run(
    "server.entrypoints:admin",
    factory=True,
    host=os.environ.get("HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "3000")),
    reload=args.reload,
    workers=1,
    access_log=False,
)
