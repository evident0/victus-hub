"""Hardware detection; ports hardware.rs exactly."""

PRODUCT_NAME_PATH = "/sys/devices/virtual/dmi/id/product_name"
BOARD_NAME_PATH = "/sys/devices/virtual/dmi/id/board_name"


def _read_trimmed_sysfs(path: str) -> str | None:
    try:
        with open(path) as f:
            value = f.read().strip()
    except OSError:
        return None
    return value if value else None


def _format_hardware_title(product: str, board: str) -> str:
    if board in product:
        return product
    return f"{product} ({board})"


def board_title() -> str:
    """Short board id for the settings header, e.g. 'Board 8BD4'."""
    board = _read_trimmed_sysfs(BOARD_NAME_PATH)
    return f"Board {board}" if board else ""


def hardware_title() -> str:
    product = _read_trimmed_sysfs(PRODUCT_NAME_PATH)
    board = _read_trimmed_sysfs(BOARD_NAME_PATH)

    if product and board:
        return _format_hardware_title(product, board)
    elif product:
        return product
    elif board:
        return board
    else:
        return "HP Laptop"
