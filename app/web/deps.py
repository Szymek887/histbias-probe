"""Shared route dependencies."""

from fastapi import Request

from app.crypto import Cipher


def get_cipher(request: Request) -> Cipher | None:
    return request.app.state.cipher


def cipher_error(request: Request) -> str | None:
    return request.app.state.cipher_error
