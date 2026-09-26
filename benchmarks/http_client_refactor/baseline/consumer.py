"""An existing caller; the refactor must not change this file."""
from src.http_client import fetch_order, fetch_user, post_event

__all__ = ['fetch_order', 'fetch_user', 'post_event']
