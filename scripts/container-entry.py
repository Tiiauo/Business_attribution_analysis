import os
import sys
from pathlib import Path
from urllib.parse import quote

password = Path("/run/secrets/postgres_password").read_text().strip()
os.environ["BA_DATABASE_URL"] = f"postgresql+asyncpg://ba_app:{quote(password, safe='')}@db:5432/ba_compose"
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
