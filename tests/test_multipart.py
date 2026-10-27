from pathlib import Path

import pytest

from studio.multipart import MultipartFiles


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 64 * 1024, 1024 * 1024])
def test_streaming_parts_preserve_binary_data_and_filenames(tmp_path: Path, chunk_size: int) -> None:
    binary = b"\x00\xff\r\n--mediaX\r\n--media--X\r\n" + b"x" * 130_000
    body = (
        b'--media\r\nContent-Disposition: form-data; name="note"\r\n\r\nignored\r\n'
        b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n\r\n'
        + binary
        + b"\r\n--media\r\nContent-Disposition: form-data; name=\"files\"; filename*=UTF-8''caf%C3%A9.png\r\n\r\nimage"
        b"\r\n--media--\r\n"
    )
    parser = MultipartFiles('multipart/form-data; boundary="media"', tmp_path)
    try:
        for start in range(0, len(body), chunk_size):
            parser.feed(body[start:start + chunk_size])
        files = parser.finish()
    finally:
        parser.close()
    assert [name for name, _ in files] == ["clip.mp4", "café.png"]
    assert files[0][1].read_bytes() == binary
    assert files[1][1].read_bytes() == b"image"


@pytest.mark.parametrize("content_type", [
    "multipart/form-data", "multipart/form-data; boundary=", "multipart/form-data; boundary=" + "x" * 71,
])
def test_invalid_boundary_is_rejected(tmp_path: Path, content_type: str) -> None:
    with pytest.raises(ValueError, match="boundary"):
        MultipartFiles(content_type, tmp_path)


def test_large_headers_are_rejected(tmp_path: Path) -> None:
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    with pytest.raises(ValueError, match="headers exceed"):
        parser.feed(b"--media\r\nX-Header: " + b"x" * (17 * 1024))


def test_excessive_part_count_is_rejected(tmp_path: Path) -> None:
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    part = b'--media\r\nContent-Disposition: form-data; name="note"\r\n\r\nx\r\n'
    with pytest.raises(ValueError, match="too many"):
        parser.feed(part * 101 + b"--media--\r\n")


@pytest.mark.parametrize("chunk_size", [1, 7, 64 * 1024])
def test_encoded_names_utf8_filenames_preamble_and_padding(tmp_path: Path, chunk_size: int) -> None:
    body = (
        b"a MIME preamble\r\n--media \t\r\n"
        b"Content-Disposition: form-data; name*=UTF-8''files; filename=\"caf\xc3\xa9.png\"\r\n\r\n"
        b"image\r\n--media\t \r\n"
        b'Content-Disposition: form-data; name="files"; filename="clip.mp4"\r\n\r\n'
        b"video\r\n--media-- \t\r\nan epilogue"
    )
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    try:
        for start in range(0, len(body), chunk_size):
            parser.feed(body[start:start + chunk_size])
        files = parser.finish()
    finally:
        parser.close()
    assert [name for name, _ in files] == ["caf\u00e9.png", "clip.mp4"]
    assert [path.read_bytes() for _, path in files] == [b"image", b"video"]


@pytest.mark.parametrize("encoding", ["base64", "quoted-printable", "unknown"])
def test_encoded_part_is_rejected_before_opening_a_file(tmp_path: Path, encoding: str) -> None:
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    body = (
        b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n'
        + f"Content-Transfer-Encoding: {encoding}\r\n\r\nencoded\r\n--media--\r\n".encode()
    )
    with pytest.raises(ValueError, match="Content-Transfer-Encoding"):
        parser.feed(body)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("header", [
    b"Invalid header", b"Bad Header: value", b" orphaned continuation",
    b'Content-Disposition: form-data; name="files"; filename="unterminated',
])
def test_malformed_headers_are_rejected_before_opening_a_file(tmp_path: Path, header: bytes) -> None:
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    with pytest.raises(ValueError, match="headers"):
        parser.feed(
            b'--media\r\n' + header
            + b'\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n\r\ndata'
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("encoding", ["7bit", "8bit", "binary"])
def test_identity_transfer_encoding_is_accepted(tmp_path: Path, encoding: str) -> None:
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    try:
        parser.feed(
            b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n'
            + f"Content-Transfer-Encoding: {encoding}\r\n\r\ndata\r\n--media--\r\n".encode()
        )
        assert parser.finish()[0][1].read_bytes() == b"data"
    finally:
        parser.close()


def test_file_limit_is_separate_from_field_limit(tmp_path: Path) -> None:
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    field = b'--media\r\nContent-Disposition: form-data; name="note"\r\n\r\nx\r\n'
    file = b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n\r\nx\r\n'
    try:
        parser.feed(field * 10 + file * 1000 + b"--media--\r\n")
        assert len(parser.finish()) == 1000
    finally:
        parser.close()


def test_more_than_a_thousand_files_are_rejected(tmp_path: Path) -> None:
    parser = MultipartFiles("multipart/form-data; boundary=media", tmp_path)
    file = b'--media\r\nContent-Disposition: form-data; name="files"; filename="clip.mp4"\r\n\r\nx\r\n'
    try:
        with pytest.raises(ValueError, match="too many.*files"):
            parser.feed(file * 1001 + b"--media--\r\n")
    finally:
        parser.close()
