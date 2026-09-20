import asyncio
import unittest

from doppel_agent.concurrency import ResourceLimits


class ResourceLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_profiles_have_independent_limits(self):
        limits = ResourceLimits(model_calls_per_profile=1)
        active = {"a": 0, "b": 0}
        peaks = {"a": 0, "b": 0}

        async def call(profile):
            async with limits.provider(profile):
                active[profile] += 1
                peaks[profile] = max(peaks[profile], active[profile])
                await asyncio.sleep(0.02)
                active[profile] -= 1

        await asyncio.gather(call("a"), call("a"), call("b"), call("b"))
        self.assertEqual(peaks, {"a": 1, "b": 1})
