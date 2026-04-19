from pathlib import Path

ROOT = Path.home() / ".parlai"
DB_PATH = ROOT / "db.sqlite"
RAW_DIR = ROOT / "raw"
CREDS_PATH = ROOT / "credentials.json"


def ensure() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def raw_path(provider: str, conv_id: str) -> Path:
    d = RAW_DIR / provider
    d.mkdir(parents=True, exist_ok=True)
    safe = conv_id.replace("/", "_")
    return d / f"{safe}.json"
