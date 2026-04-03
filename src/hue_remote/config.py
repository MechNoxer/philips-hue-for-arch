from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "hue-remote"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class BridgeConfig:
    bridge_ip: str = ""
    username: str = ""


def load_config() -> BridgeConfig:
    if not CONFIG_FILE.exists():
        return BridgeConfig()

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BridgeConfig()

    return BridgeConfig(
        bridge_ip=data.get("bridge_ip", ""),
        username=data.get("username", ""),
    )


def save_config(config: BridgeConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")
