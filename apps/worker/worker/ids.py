"""nanoid generator matching the JS `nanoid(12)` used by Drizzle."""

from nanoid import generate

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_-"


def new_id(size: int = 12) -> str:
    return generate(ALPHABET, size)
