import os
from cryptography.fernet import Fernet

_key = os.getenv("ENCRYPTION_KEY", "")

def _get_fernet() -> Fernet:
    key = _key or os.getenv("ENCRYPTION_KEY", "")
    if not key:
        # Generate and use a temporary key (not safe for production — set ENCRYPTION_KEY in env)
        key = Fernet.generate_key().decode()
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
