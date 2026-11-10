"""Run the API: python -m citebook"""

import uvicorn

from citebook.api import create_app
from citebook.config import Settings

# A constant, not a setting: v1 has no authentication, so it must never listen beyond this machine.
HOST = "127.0.0.1"


def main() -> None:
    settings = Settings()
    uvicorn.run(create_app(settings), host=HOST, port=settings.port)


if __name__ == "__main__":
    main()
