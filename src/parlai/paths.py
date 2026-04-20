from pathlib import Path

ROOT = Path.home() / ".parlai"
CREDS_PATH = ROOT / "credentials.json"


def ensure() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
