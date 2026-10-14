import logging
import os

import uvicorn


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO; keep it quiet so logs stay about listings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run("openmetadata_sync.app:create_app", factory=True, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "3011")), proxy_headers=True, access_log=False)


if __name__ == "__main__":
    main()
