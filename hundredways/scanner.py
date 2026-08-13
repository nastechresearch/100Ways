"""File format identification via magic bytes.

Identifies any file type by its signature (magic bytes), not by extension.
Covers images (PNG, JPEG, GIF, WebP, SVG, ICO, BMP, TIFF, AVIF, HEIC),
archives, executables, fonts, docs, audio, video, code, and lockfiles.

Used by the watcher to flag binary assets (e.g. logos/frames that must be
renamed-but-not-rebranded) and to decide which files need content branding.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileType:
    fmt: str
    category: str  # image | binary | archive | font | doc | audio | video | text | lockfile
    description: str = ""

    @property
    def is_image(self) -> bool:
        return self.category == "image"

    @property
    def is_binary(self) -> bool:
        return self.category in ("binary", "image", "archive", "font", "audio", "video")


# signature -> FileType.  Byte signatures are bytes() prefixes.
_MAGIC: list[tuple[bytes, FileType]] = [
    # images
    (b"\x89PNG\r\n\x1a\n", FileType("png", "image", "PNG image")),
    (b"\xff\xd8\xff", FileType("jpeg", "image", "JPEG image")),
    (b"GIF87a", FileType("gif", "image", "GIF image")),
    (b"GIF89a", FileType("gif", "image", "GIF image")),
    (b"RIFF", FileType("webp", "image", "WebP image")),  # confirmed by fmt check
    (b"BM", FileType("bmp", "image", "BMP image")),
    (b"II*\x00", FileType("tiff", "image", "TIFF image")),
    (b"MM\x00*", FileType("tiff", "image", "TIFF image")),
    (b"8BPS", FileType("psd", "image", "Photoshop image")),
    (b"\x00\x00\x01\x00", FileType("ico", "image", "ICO/cur icon")),
    (b"ftypavif", FileType("avif", "image", "AVIF image")),
    (b"ftypheic", FileType("heic", "image", "HEIC image")),
    (b"ftypheif", FileType("heif", "image", "HEIF image")),
    (b"ftypmif1", FileType("heif", "image", "HEIF image")),
    # archives
    (b"PK\x03\x04", FileType("zip", "archive", "ZIP archive")),
    (b"PK\x05\x06", FileType("zip", "archive", "ZIP archive (empty)")),
    (b"\x1f\x8b", FileType("gzip", "archive", "gzip archive")),
    (b"BZh", FileType("bz2", "archive", "bzip2 archive")),
    (b"\xfd7zXZ\x00", FileType("xz", "archive", "xz archive")),
    (b"7z\xbc\xaf\x27\x1c", FileType("7z", "archive", "7-Zip archive")),
    (b"Rar!\x1a\x07", FileType("rar", "archive", "RAR archive")),
    (b"ustar", FileType("tar", "archive", "tar archive")),
    (b"\x1f\x8b\x08", FileType("tar.gz", "archive", "gzipped tar archive")),
    # executables / binaries
    (b"\x7fELF", FileType("elf", "binary", "ELF executable")),
    (b"MZ", FileType("pe", "binary", "PE/Windows executable")),
    (b"\xca\xfe\xba\xbe", FileType("macho-fat", "binary", "Mach-O universal binary")),
    (b"\xcf\xfa\xed\xfe", FileType("macho", "binary", "Mach-O executable")),
    (b"\xfe\xed\xfa\xce", FileType("macho", "binary", "Mach-O executable")),
    (b"n\xa3\x00\x00", FileType("class", "binary", "Java class")),
    (b"d8\x01\x00", FileType("class", "binary", "Java class")),
    # fonts
    (b"\x00\x01\x00\x00", FileType("ttf", "font", "TrueType font")),
    (b"OTTO", FileType("otf", "font", "OpenType font")),
    (b"wOFF", FileType("woff", "font", "WOFF font")),
    (b"wOF2", FileType("woff2", "font", "WOFF2 font")),
    (b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", FileType("eot", "font", "EOT font")),  # noqa: E501
    # docs
    (b"%PDF", FileType("pdf", "doc", "PDF document")),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", FileType("ole2", "doc", "OLE2/Office (doc/xls/ppt)")),
    (b"PK\x03\x04", FileType("ooxml", "doc", "Office Open XML")),  # conflicts zip; docx handled in detect()
    (b"\x7b\x5c\x72\x74\x66", FileType("rtf", "doc", "RTF document")),
    # audio / video
    (b"ID3", FileType("mp3", "audio", "MP3 audio")),
    (b"\xff\xfb", FileType("mp3", "audio", "MP3 audio")),
    (b"OggS", FileType("ogg", "audio", "Ogg audio/video")),
    (b"fLaC", FileType("flac", "audio", "FLAC audio")),
    (b"RIFF", FileType("wav", "audio", "WAV audio")),  # confirmed by fmt
    (b"\x00\x00\x00\x1cftypisom", FileType("mp4", "video", "MP4 video")),
    (b"\x00\x00\x00\x1cftyp", FileType("mp4", "video", "MP4 video")),
    (b"fLaC", FileType("flac", "audio", "FLAC audio")),
    (b"\x1aE\xdf\xa3", FileType("mkv", "video", "Matroska video")),
    (b"\x00\x00\x01\xba", FileType("mpeg-ps", "video", "MPEG program stream")),
    # SQLite / DB
    (b"SQLite format 3\x00", FileType("sqlite", "binary", "SQLite database")),
]


def detect(data: bytes) -> FileType | None:
    """Identify a file's format from its leading bytes."""
    if not data:
        return None
    for magic, ftype in _MAGIC:
        if data.startswith(magic):
            if ftype.fmt == "webp" and not data.startswith(b"RIFF\x00\x00\x00\x00WEBP"):
                continue
            if ftype.fmt == "wav" and not data.startswith(b"RIFF"):
                continue
            if ftype.fmt == "ooxml" and not (
                data[30:38] == b"[Content"
                or data[30:38].startswith(b"[")
            ):
                continue
            return ftype
    return None


def classify_path(data: bytes, path: str) -> FileType:
    """Detect a file's type, falling back to extension hints, then text.

    Returns a ``FileType`` with category "text" for plain text / code and
    "binary" for anything unrecognized but containing NUL bytes.
    """
    ft = detect(data)
    if ft is not None:
        return ft

    if b"\x00" in data[:8192]:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return FileType(ext or "bin", "binary", f"unknown binary (.{ext})")

    return FileType("text", "text", "text / code")


def is_text(data: bytes) -> bool:
    """Heuristic: a blob is text if it has no NUL bytes and decodes as UTF-8."""
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False
