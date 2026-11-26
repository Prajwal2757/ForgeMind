"""Model providers against a local stand-in server (no real API calls)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from forgemind import net
from forgemind.embeddings import OpenAIEmbedder
from forgemind.llm import AnthropicLLM, OpenAILLM
from forgemind.net import ProviderError


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    state = {"replies": [], "seen": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state["seen"].append((self.path, dict(self.headers), body))
            status, reply = state["replies"].pop(0)
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "http://127.0.0.1:1/steal")
            self.end_headers()
            self.wfile.write(json.dumps(reply).encode())

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}"
    yield state
    httpd.shutdown()
    httpd.server_close()


def test_anthropic_request_and_retry(server):
    server["replies"] = [(529, {}), (200, {"content": [{"type": "thinking", "thinking": "hm"},
                                                      {"type": "text", "text": "hi [1]"}]})]
    assert AnthropicLLM("key", "m", server["url"]).complete("sys", "q") == "hi [1]"
    path, headers, body = server["seen"][1]
    assert path == "/v1/messages" and headers["X-Api-Key"] == "key" and body["system"] == "sys"


@pytest.mark.parametrize("replies,calls", [([(401, {})], 1), ([(503, {})] * 3, 3),
                                           ([(200, {"oops": 1})], 1), ([(302, {})], 1)])
def test_failures_become_provider_errors(server, replies, calls):
    server["replies"] = list(replies)
    with pytest.raises(ProviderError):
        OpenAILLM(server["url"], "k", "m").complete("s", "q")
    assert len(server["seen"]) == calls


def test_openai_chat_plan_and_embeddings(server):
    server["replies"] = [
        (200, {"choices": [{"message": {"content": '```json\n["a", "b"]\n```'}}]}),
        (200, {"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 0]}]})]
    assert OpenAILLM(server["url"], "k", "m").plan("a and b") == ["a", "b"]
    assert OpenAIEmbedder(server["url"], "k", "m").embed(["x", "y"]).tolist() == [[1, 0], [0, 1]]
