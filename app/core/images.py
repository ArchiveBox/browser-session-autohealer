"""Use actual screenshot bytes: cloud browsers can ignore the requested format."""


def screenshot_format(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    raise ValueError("The browser did not return a PNG or JPEG screenshot")
