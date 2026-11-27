"""JSON POST with timeouts and retries for model providers."""
import http.client
import json
import logging
import random
import time
import urllib.error
import urllib.request

log = logging.getLogger("forgemind.net")
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class ProviderError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):  # never forward API keys elsewhere
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def post_json(url, headers, body, timeout=60, attempts=3, backoff=1.0):
    data = json.dumps(body).encode()
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data, {"Content-Type": "application/json", **headers})
            with _opener.open(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            reason, retry = f"HTTP {exc.code}", exc.code in RETRY_STATUS
        except (OSError, http.client.HTTPException) as exc:
            reason, retry = type(exc).__name__, True
        except ValueError:
            reason, retry = "invalid JSON in response", False
        log.warning("model provider request failed (%s), attempt %d", reason, attempt)
        if not retry or attempt == attempts:
            raise ProviderError(reason)
        time.sleep(backoff * 2 ** (attempt - 1) * (1 + random.random() / 4))
