import hashlib
import hmac
import json
from urllib.parse import parse_qsl


def verify_init_data(init_data: str, bot_token: str) -> dict | None:
    """
    Верифицирует Telegram WebApp initData по HMAC-SHA256.
    Возвращает распарсенные данные или None если подпись неверна.
    """
    if not init_data:
        return None

    parsed = dict(parse_qsl(init_data, strict_parsing=False))
    hash_received = parsed.pop("hash", None)
    if not hash_received:
        return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )

    secret_key = hmac.new(
        b"WebAppData", bot_token.encode(), hashlib.sha256
    ).digest()
    computed_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, hash_received):
        return None

    # Распарсим user из JSON-строки
    if "user" in parsed:
        try:
            parsed["user"] = json.loads(parsed["user"])
        except Exception:
            pass

    return parsed
