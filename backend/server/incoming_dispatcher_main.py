"""来文专用 Dispatcher 入口。"""

import asyncio

from yuxi.services.incoming_ingest_dispatcher_service import run_dispatcher


if __name__ == "__main__":
    asyncio.run(run_dispatcher())
