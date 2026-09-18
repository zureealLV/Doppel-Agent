import json
import unittest

from doppel_agent.protocol import ProtocolError, parse_request


class ProtocolTests(unittest.TestCase):
    def test_valid_request(self):
        request = parse_request(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}))
        self.assertEqual(request["method"], "ping")

    def test_invalid_json(self):
        with self.assertRaises(ProtocolError) as caught:
            parse_request("{")
        self.assertEqual(caught.exception.code, -32700)

    def test_invalid_params(self):
        with self.assertRaises(ProtocolError) as caught:
            parse_request('{"jsonrpc":"2.0","id":1,"method":"run","params":[]}')
        self.assertEqual(caught.exception.code, -32600)
