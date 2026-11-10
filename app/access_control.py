def is_allowed_user(user_id: int, allowed_ids: str) -> bool:
    """Return whether a Telegram user ID is explicitly allowlisted."""
    if not allowed_ids:
        return False
    allowed = set()
    for value in allowed_ids.split(","):
        value = value.strip()
        if value.isdigit():
            allowed.add(int(value))
    return user_id in allowed
