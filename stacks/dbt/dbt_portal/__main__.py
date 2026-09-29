import logging
import os

import uvicorn


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run("dbt_portal.app:create_app", factory=True, host=os.environ.get("HOST", "0.0.0.0"),
                port=int(os.environ.get("PORT", "3004")), proxy_headers=True, access_log=False)


if __name__ == "__main__":
    main()
