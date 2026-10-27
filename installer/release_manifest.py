"""Generate and authenticate stable release metadata without exposing signing keys."""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stable_version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise ValueError("A stable release requires a three-part version without a suffix")
    major, minor, patch = map(int, value.split("."))
    return major, minor, patch


def https_url(value: str) -> None:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or any(character.isspace() or ord(character) < 32 for character in value)):
        raise ValueError("Release URLs must use HTTPS without credentials or fragments")


def validate(document: dict) -> None:
    if set(document) != {"version", "channel", "publishedAt", "installer", "minimumVersion", "releaseNotesUrl"}:
        raise ValueError("Invalid release manifest fields")
    if document["channel"] != "stable":
        raise ValueError("Only stable releases are supported")
    for name in ("version", "minimumVersion", "publishedAt", "releaseNotesUrl"):
        if not isinstance(document[name], str):
            raise ValueError("Invalid release manifest field type")
    if stable_version(document["minimumVersion"]) > stable_version(document["version"]):
        raise ValueError("Minimum supported version exceeds the release version")
    published = document["publishedAt"]
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", published):
        raise ValueError("Publication date must be a UTC timestamp")
    datetime.fromisoformat(published)
    https_url(document["releaseNotesUrl"])
    installer = document["installer"]
    if not isinstance(installer, dict) or set(installer) != {"url", "size", "sha256"}:
        raise ValueError("Invalid installer metadata")
    if (not isinstance(installer["url"], str) or type(installer["size"]) is not int or installer["size"] <= 0
            or not isinstance(installer["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", installer["sha256"])):
        raise ValueError("Invalid installer size or SHA-256")
    https_url(installer["url"])


def generate(installer: Path, *, version: str, url: str, minimum_version: str,
             release_notes_url: str, published_at: str | None = None) -> bytes:
    document = {"version": version, "channel": "stable",
                "publishedAt": published_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "installer": {"url": url, "size": installer.stat().st_size, "sha256": digest(installer)},
                "minimumVersion": minimum_version, "releaseNotesUrl": release_notes_url}
    validate(document)
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def decode_key(value: str) -> bytes:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("An Ed25519 key must be base64-encoded raw bytes") from None
    if len(raw) != 32:
        raise ValueError("An Ed25519 key must contain exactly 32 raw bytes")
    return raw


def keygen(private_path: Path, public_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
    if private_path.resolve() == public_path.resolve() or private_path.exists() or public_path.exists():
        raise ValueError("Choose distinct, new private and public key files")
    if not private_path.parent.is_dir() or not public_path.parent.is_dir():
        raise ValueError("Both key destinations require existing parent directories")
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    # Exclusive creation prevents an accidental key rotation; never emit private bytes to stdout.
    with public_path.open("x", encoding="ascii") as stream:
        stream.write(base64.b64encode(public).decode("ascii") + "\n")
    descriptor = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(base64.b64encode(private).decode("ascii") + "\n")


def sign(data: bytes, encoded_key: str) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    validate(json.loads(data))
    return Ed25519PrivateKey.from_private_bytes(decode_key(encoded_key)).sign(data)


def verify(data: bytes, signature: bytes, encoded_public_key: str, installer: Path | None = None) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(decode_key(encoded_public_key)).verify(signature, data)
    except InvalidSignature:
        raise ValueError("Release manifest signature verification failed") from None
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError("Invalid release manifest")
    validate(document)
    if installer is not None and (installer.stat().st_size != document["installer"]["size"]
                                  or digest(installer) != document["installer"]["sha256"]):
        raise ValueError("Installer size or SHA-256 does not match the signed manifest")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    keys = commands.add_parser("keygen")
    keys.add_argument("--private-key", type=Path, required=True)
    keys.add_argument("--public-key", type=Path, required=True)
    create = commands.add_parser("generate")
    create.add_argument("--installer", type=Path, required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--url", required=True)
    create.add_argument("--minimum-version", required=True)
    create.add_argument("--release-notes-url", required=True)
    create.add_argument("--published-at")
    create.add_argument("--out", type=Path, required=True)
    signing = commands.add_parser("sign")
    signing.add_argument("manifest", type=Path)
    signing.add_argument("--out", type=Path, required=True)
    checking = commands.add_parser("verify")
    checking.add_argument("manifest", type=Path)
    checking.add_argument("--signature", type=Path, required=True)
    checking.add_argument("--public-key", type=Path, required=True)
    checking.add_argument("--installer", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            keygen(args.private_key, args.public_key)
        elif args.command == "generate":
            args.out.write_bytes(generate(args.installer, version=args.version, url=args.url,
                                         minimum_version=args.minimum_version, release_notes_url=args.release_notes_url,
                                         published_at=args.published_at))
        elif args.command == "sign":
            key = os.environ.get("RELEASE_ED25519_KEY", "")
            if not key:
                raise ValueError("RELEASE_ED25519_KEY is required")
            args.out.write_bytes(sign(args.manifest.read_bytes(), key))
        else:
            verify(args.manifest.read_bytes(), args.signature.read_bytes(),
                   args.public_key.read_text(encoding="ascii"), args.installer)
    except (OSError, ValueError, TypeError, KeyError) as error:
        message = "Release metadata signing failed; check the inputs and key configuration." if args.command == "sign" else str(error)
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
