"""客户端 API 入口。

    docker compose up -d nats   # 控制面仍独立；这个进程只服务用户可见的聊天
    .venv/bin/python -m services.clientapi.main
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from services.worker.model import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, ModelConfig, select_model

from .server import ClientApp, serve
from .store import ProjectStore


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    _load_dotenv(Path(".env"))
    cfg = ModelConfig(
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
        model=os.environ.get("NVIDIA_MODEL", DEFAULT_MODEL),
        temperature=float(os.environ.get("NVIDIA_TEMPERATURE", "1")),
        max_tokens=int(os.environ.get("NVIDIA_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
        timeout=float(os.environ.get("NVIDIA_TIMEOUT", "300")),
    )
    state = Path(os.environ.get("CLIENT_STATE", "data/client-state.json"))
    swarms = Path(os.environ.get("SWARM_DIR", "examples/swarms"))
    store = ProjectStore.open(state, swarms)
    app = ClientApp(store, select_model(cfg), max_tokens=cfg.max_tokens)
    host = os.environ.get("CLIENT_API_HOST", "127.0.0.1")
    port = int(os.environ.get("CLIENT_API_PORT", "8787"))
    logging.getLogger(__name__).info(
        "model source: %s",
        cfg.model if cfg.api_key else "stub",
    )
    serve(app, host, port)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    main()
