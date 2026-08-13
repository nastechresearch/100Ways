from hundredways.scanner import classify_path, detect, is_text

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
ZIP = b"PK\x03\x04" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
PY = b"# a python file\nprint('hi')\n"


def test_detect_png():
    assert detect(PNG).fmt == "png"


def test_detect_jpeg():
    assert detect(JPEG).fmt == "jpeg"


def test_detect_zip():
    assert detect(ZIP).fmt == "zip"


def test_detect_gif():
    assert detect(GIF).fmt == "gif"


def test_classify_image_category():
    ft = classify_path(PNG, "logo.png")
    assert ft.category == "image"


def test_classify_archive_category():
    assert classify_path(ZIP, "x.zip").category == "archive"


def test_is_text_detects_python():
    assert is_text(PY)


def test_is_text_rejects_binary():
    assert not is_text(PNG)


def test_unknown_binary_format():
    ft = classify_path(b"\x00\x01\x02", "mystery.bin")
    assert ft.category == "binary"


def test_unknown_text_defaults_to_text():
    ft = classify_path(b"just text", "notes.txt")
    assert ft.fmt == "text" and ft.category == "text"


def test_extension_hint_when_magic_missing():
    ft = classify_path(b"PK\x00\x00", "archive.zip")
    assert ft.fmt == "zip" or ft.category == "archive"
