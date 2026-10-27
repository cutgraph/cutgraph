from __future__ import annotations

import email
import re
from email import policy
from email.parser import HeaderParser
from pathlib import Path
from typing import BinaryIO
from pipeline.errors import ValidationError


class MultipartFiles:
    """Incrementally write file parts, retaining only headers and a boundary-sized tail."""

    def __init__(self, content_type: str, directory: Path) -> None:
        header = email.message_from_string(f"Content-Type: {content_type}\n\n")
        boundary = header.get_boundary()
        if not boundary or len(boundary) > 70 or not boundary.isascii() or "\r" in boundary or "\n" in boundary:
            raise ValidationError("invalid multipart boundary")
        self._boundary = b"--" + boundary.encode("ascii")
        self._marker = b"\r\n" + self._boundary
        self._directory = directory
        self._buffer = bytearray(b"\r\n")
        self._state = "preamble"
        self._file: BinaryIO | None = None
        self._files = 0
        self._fields = 0
        self.files: list[tuple[str, Path]] = []

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def feed(self, chunk: bytes) -> None:
        # ASGI may deliver a large chunk; the parser itself never retains that entire chunk.
        for offset in range(0, len(chunk), 64 * 1024):
            if self._state == "done":
                return
            self._buffer.extend(memoryview(chunk)[offset:offset + 64 * 1024])
            self._parse()

    def finish(self) -> list[tuple[str, Path]]:
        self._parse(final=True)
        if self._state != "done":
            raise ValidationError("incomplete multipart body")
        return self.files

    def _write(self, size: int) -> None:
        if self._file is not None:
            self._file.write(self._buffer[:size])
        del self._buffer[:size]

    def _parse(self, *, final: bool = False) -> None:
        while True:
            if self._state == "headers":
                end = self._buffer.find(b"\r\n\r\n")
                if end > 16 * 1024 or (end < 0 and len(self._buffer) > 16 * 1024):
                    raise ValidationError("multipart headers exceed the size limit")
                if end < 0:
                    return
                raw = bytes(self._buffer[:end])
                for index, line in enumerate(raw.split(b"\r\n")):
                    if line.startswith((b" ", b"\t")) and index:
                        continue
                    if not re.match(rb"^[!#$%&'*+.^_`|~0-9A-Za-z-]+:", line):
                        raise ValidationError("invalid multipart headers")
                try:
                    headers = HeaderParser(policy=policy.default).parsestr(raw.decode("utf-8"))
                except UnicodeDecodeError as e:
                    raise ValidationError("invalid multipart headers") from e
                if headers.defects or any(getattr(value, "defects", ()) for value in headers.values()):
                    raise ValidationError("invalid multipart headers")
                for name in ("Content-Disposition", "Content-Transfer-Encoding"):
                    if len(headers.get_all(name, [])) > 1:
                        raise ValidationError("invalid multipart headers")
                del self._buffer[:end + 4]
                if headers.get_content_disposition() != "form-data":
                    raise ValidationError("expected multipart/form-data")
                encoding = headers.get("Content-Transfer-Encoding", "binary").strip().lower()
                if encoding not in {"7bit", "8bit", "binary"}:
                    raise ValidationError("unsupported Content-Transfer-Encoding")
                filename = headers.get_filename()
                if filename is not None:
                    self._files += 1
                    if self._files > 1000:
                        raise ValidationError("too many multipart files")
                else:
                    self._fields += 1
                    if self._fields > 20:
                        raise ValidationError("too many multipart fields")
                if headers.get_param("name", header="content-disposition") == "files" and filename:
                    path = self._directory / f"{self._files}.part"
                    self._file = path.open("xb")
                    self.files.append((filename, path))
                self._state = "data"
            elif self._state in {"data", "preamble"}:
                start = 0
                while True:
                    end = self._buffer.find(self._marker, start)
                    if end < 0:
                        # Retain a potential delimiter split across the next receive.
                        self._write(max(0, len(self._buffer) - len(self._marker) - 4))
                        return
                    delimiter = self._delimiter(end + len(self._marker), final=final)
                    if delimiter is None:
                        self._write(end)
                        return
                    after, closing = delimiter
                    if after >= 0:
                        break
                    start = end + len(self._marker)
                self._write(end)
                del self._buffer[:after - end]
                self.close()
                self._state = "done" if closing else "headers"
                if closing:
                    self._buffer.clear()
            else:
                return

    def _delimiter(self, start: int, *, final: bool) -> tuple[int, bool] | None:
        if len(self._buffer) < start + 2 and not final:
            return None
        closing = self._buffer[start:start + 2] == b"--"
        after = start + 2 if closing else start
        while after < len(self._buffer) and self._buffer[after] in (32, 9):
            after += 1
            if after - start > 16 * 1024:
                raise ValidationError("multipart boundary padding exceeds the size limit")
        if after == len(self._buffer) and closing and final:
            return after, True
        if len(self._buffer) < after + 2:
            return (-1, False) if final else None
        if self._buffer[after:after + 2] == b"\r\n":
            return after + 2, closing
        return -1, False
