"""Small client with a shared transport policy."""
import logging
import time

import requests

BASE_URL = "https://service.example.invalid"
DEFAULT_TIMEOUT = 10
logger = logging.getLogger(__name__)


class RequestError(RuntimeError):
    """A transport, HTTP status, or JSON decoding failure."""


def request_json(method, url, **kwargs):
    timeout = kwargs.pop("timeout", DEFAULT_TIMEOUT)
    for attempt in range(3):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.RequestException, ValueError) as exc:
            response = getattr(exc, "response", None)
            retryable = isinstance(exc, (requests.exceptions.ConnectionError,
                                          requests.exceptions.Timeout))
            retryable = retryable or (
                isinstance(exc, requests.exceptions.HTTPError)
                and response is not None and 500 <= response.status_code < 600
            )
            if method.upper() == "GET" and retryable and attempt < 2:
                logger.warning("Retrying request")
                time.sleep(0.1 * (attempt + 1))
                continue
            logger.error("Request failed")
            raise RequestError("Request failed") from exc


def fetch_user(user_id):
    return request_json("GET", f"{BASE_URL}/users/{user_id}", timeout=5)


def fetch_order(order_id):
    return request_json("GET", f"{BASE_URL}/orders/{order_id}")


def post_event(event):
    return request_json("POST", f"{BASE_URL}/events", json=event)
