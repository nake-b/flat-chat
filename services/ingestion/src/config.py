import os

from dotenv import load_dotenv

# Walks upward from the cwd until it finds a `.env` (the project root).
# Existing env vars (e.g. set by docker compose) are NOT overridden.
load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
