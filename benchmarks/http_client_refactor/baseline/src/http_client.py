"""Small existing client; requests is an existing application dependency."""
import requests

BASE_URL = "https://service.example.invalid"


def fetch_user(user_id):
    response = requests.get(f"{BASE_URL}/users/{user_id}", timeout=5)
    response.raise_for_status()
    return response.json()


def fetch_order(order_id):
    response = requests.get(f"{BASE_URL}/orders/{order_id}", timeout=10)
    response.raise_for_status()
    return response.json()


def post_event(event):
    response = requests.post(f"{BASE_URL}/events", json=event, timeout=10)
    response.raise_for_status()
    return response.json()
