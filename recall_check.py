import asyncio
import services
from termitaria.memory.v1 import memory_pb2
from services.memory.backend import build_backend
from services.memory.config import MemoryConfig

def show(title, resp):
    print(f"\n== {title} ==")
    for it in resp.items:
        print(memory_pb2.MemoryKind.Name(it.kind), it.text)

async def main():
    cfg = MemoryConfig.from_env()
    backend = await build_backend(cfg)
    print("backend", backend.name, "degraded", backend.degraded)
    req = memory_pb2.RecallRequest(
        agent_id="lab_test",
        query_text="实验室编号是多少",
        depth=memory_pb2.RECALL_DEPTH_AUTO,
        top_k=5,
    )
    show("before", await backend.recall(req))
    episode = memory_pb2.Episode(
        agent_id="lab_test",
        session_id="s_test",
        summary="实验室编号是 17-BLUE",
    )
    await backend.write_episode(memory_pb2.WriteEpisodeRequest(episode=episode))
    show("after", await backend.recall(req))

asyncio.run(main())