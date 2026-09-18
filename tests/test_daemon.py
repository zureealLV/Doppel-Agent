import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import unittest

from doppel_agent.core import Core
from doppel_agent.daemon import handle_client
from support import workspace


class DaemonTests(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_rpc(self):
        with workspace() as root:
            core = Core(root)
            server = await asyncio.start_server(
                lambda reader, writer: handle_client(reader, writer, core), "127.0.0.1", 0
            )
            try:
                port = server.sockets[0].getsockname()[1]
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                request = {"jsonrpc": "2.0", "id": 7, "method": "run", "params": {"prompt": "hello"}}
                writer.write((json.dumps(request) + "\n").encode())
                await writer.drain()
                response = json.loads(await reader.readline())
                self.assertEqual(response["id"], 7)
                self.assertEqual(response["result"]["status"], "completed")
                writer.close()
                await writer.wait_closed()
            finally:
                server.close()
                await server.wait_closed()

    async def test_cli_daemon_process(self):
        with workspace() as root:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            env = os.environ.copy()
            daemon = subprocess.Popen(
                [sys.executable, "-m", "doppel_agent.cli", "serve", "--workspace", str(root), "--port", str(port)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                for _ in range(30):
                    if daemon.poll() is not None:
                        self.fail("daemon exited before becoming ready")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("daemon did not listen")
                client = subprocess.run(
                    [sys.executable, "-m", "doppel_agent.cli", "run", "hello", "--port", str(port)],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(client.returncode, 0, client.stderr + client.stdout)
                self.assertEqual(json.loads(client.stdout)["result"]["status"], "completed")
            finally:
                daemon.terminate()
                daemon.communicate(timeout=5)
