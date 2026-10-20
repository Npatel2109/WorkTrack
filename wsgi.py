from pathlib import Path

from dotenv import load_dotenv

# Gunicorn does not read .env; real environment variables still take precedence.
load_dotenv(Path(__file__).resolve().parent / ".env")

from geosite import create_app  # noqa: E402

app = create_app()
