"""Three aiohttp servers used by the 30-second integration smoke.

* healthy: always returns 200
* flaky:   alternates 200 / 500
* broken:  always returns 500

Servers run on caller-chosen ports (default 18080/18081/18082).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import cycle

from aiohttp import web


async def _healthy(_req: web.Request) -> web.Response:
    return web.Response(text="ok", status=200)


def _make_flaky() -> _Flaky:
    return _Flaky(cycle([200, 500]))


@dataclass
class _Flaky:
    cycle: Iterator[int]

    async def __call__(self, _req: web.Request) -> web.Response:
        code = next(self.cycle)
        if 200 <= code < 300:
            return web.Response(text="ok", status=code)
        return web.Response(text="flaky", status=code)


async def _broken(_req: web.Request) -> web.Response:
    return web.Response(text="broken", status=500)


@dataclass
class RunningBackends:
    healthy: web.AppRunner
    flaky: web.AppRunner
    broken: web.AppRunner

    async def stop(self) -> None:
        await asyncio.gather(
            self.healthy.cleanup(),
            self.flaky.cleanup(),
            self.broken.cleanup(),
        )


async def start_backends(
    *,
    host: str = "127.0.0.1",
    healthy_port: int = 18080,
    flaky_port: int = 18081,
    broken_port: int = 18082,
) -> RunningBackends:
    """Spin up three local HTTP servers; caller awaits ``stop()`` to clean up."""
    runners: list[web.AppRunner] = []

    async def _spawn(handler: web.RequestHandler, port: int) -> web.AppRunner:
        app = web.Application()
        app.router.add_get("/healthz", handler)  # type: ignore[arg-type]
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        runners.append(runner)
        return runner

    healthy = await _spawn(_healthy, healthy_port)
    flaky = await _spawn(_make_flaky(), flaky_port)
    broken = await _spawn(_broken, broken_port)
    return RunningBackends(healthy=healthy, flaky=flaky, broken=broken)
