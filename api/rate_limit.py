"""Shared rate limiter singleton — imported by both api.main and route modules
to avoid circular imports while still pointing at the same Limiter instance."""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
