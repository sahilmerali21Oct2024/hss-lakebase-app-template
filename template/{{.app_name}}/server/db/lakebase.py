"""Lakebase connection pool with automatic OAuth token refresh."""

import os
import asyncio
import asyncpg
from typing import Optional
from server.auth.config import get_oauth_token


class LakebasePool:
    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._demo_mode = False

    async def get_pool(self) -> Optional[asyncpg.Pool]:
        if not os.environ.get("PGHOST"):
            self._demo_mode = True
            return None
        if self._pool is None:
            await self._create_pool()
            self._refresh_task = asyncio.create_task(self._refresh_loop())
        return self._pool

    async def _create_pool(self):
        try:
            self._pool = await asyncpg.create_pool(
                host=os.environ["PGHOST"],
                port=int(os.environ.get("PGPORT", "5432")),
                database=os.environ["PGDATABASE"],
                user=os.environ["PGUSER"],
                password=get_oauth_token(),
                ssl="require",
                min_size=2,
                max_size=10,
            )
        except Exception as e:
            print(f"Lakebase connection failed: {e}")
            self._demo_mode = True

    async def _refresh_loop(self):
        while True:
            await asyncio.sleep(45 * 60)
            try:
                if self._pool:
                    await self._pool.close()
                    self._pool = None
                await self._create_pool()
            except Exception as e:
                print(f"Token refresh failed: {e}")

    @property
    def is_demo_mode(self) -> bool:
        return self._demo_mode

    async def close(self):
        if self._refresh_task:
            self._refresh_task.cancel()
        if self._pool:
            await self._pool.close()


db = LakebasePool()
