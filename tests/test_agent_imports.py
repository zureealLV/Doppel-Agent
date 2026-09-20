import importlib.util
import unittest


class AgentDependencyTests(unittest.TestCase):
    def test_agent_extra_dependencies_are_importable(self):
        missing = [
            name
            for name in (
                "aiosqlite",
                "deepagents",
                "fastapi",
                "httpx",
                "langchain",
                "langgraph",
            )
            if importlib.util.find_spec(name) is None
        ]
        if missing:
            self.skipTest(f"install doppel-agent[agent]: {', '.join(missing)}")

        import aiosqlite
        import deepagents
        import fastapi
        import httpx
        import langchain
        import langgraph

        self.assertTrue(aiosqlite)
        self.assertTrue(deepagents)
        self.assertTrue(fastapi)
        self.assertTrue(httpx)
        self.assertTrue(langchain)
        self.assertTrue(langgraph)
