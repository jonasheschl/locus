from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx


MAX_DOWNLOAD_BYTES = 50_000_000
MAX_REDIRECTS = 5
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TITLE_RE = re.compile(r"<title(?:\s[^>]*)?>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
CONTENT_TYPES = {
    "text/html": (".html", "text/html"),
    "application/xhtml+xml": (".html", "text/html"),
    "application/pdf": (".pdf", "application/pdf"),
    "text/plain": (".txt", "text/plain"),
    "text/markdown": (".txt", "text/plain"),
    "text/csv": (".csv", "text/csv"),
    "application/csv": (".csv", "text/csv"),
    "application/json": (".json", "application/json"),
    "application/ld+json": (".json", "application/json"),
}
EXTENSION_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
}


class WebIngestError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DownloadedWebSource:
    original_url: str
    final_url: str
    content: bytes
    media_type: str
    extension: str
    title: str


def extract_http_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.finditer(text):
        value = match.group(0).rstrip(".,;:!?)]}")
        if value and value not in urls:
            urls.append(value)
    return urls


async def _require_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebIngestError("Enter a valid HTTP or HTTPS URL", 400)
    if parsed.username or parsed.password:
        raise WebIngestError("URLs containing credentials are not supported", 400)
    hostname = parsed.hostname.rstrip(".").casefold()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise WebIngestError("Local and private-network URLs cannot be ingested", 400)

    try:
        addresses = {ipaddress.ip_address(hostname)}
    except ValueError:
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            resolved = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except (OSError, ValueError) as error:
            raise WebIngestError(f"Could not resolve {hostname}", 502) from error
        addresses = {
            ipaddress.ip_address(item[4][0].split("%", 1)[0]) for item in resolved
        }

    if not addresses or any(not address.is_global for address in addresses):
        raise WebIngestError("Local and private-network URLs cannot be ingested", 400)


def _content_kind(content_type: str, url: str) -> tuple[str, str]:
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type in CONTENT_TYPES:
        return CONTENT_TYPES[media_type]
    suffix = Path(urlparse(url).path).suffix.casefold()
    if media_type in {"", "application/octet-stream"} and suffix in EXTENSION_TYPES:
        normalized = ".html" if suffix == ".htm" else suffix
        return normalized, EXTENSION_TYPES[suffix]
    raise WebIngestError(
        f"Unsupported web content type: {media_type or 'unknown'}", 415
    )


def _source_title(url: str, content: bytes, media_type: str, encoding: str | None) -> str:
    if media_type == "text/html":
        decoded = content.decode(encoding or "utf-8", errors="replace")
        match = TITLE_RE.search(decoded)
        if match:
            title = html.unescape(TAG_RE.sub(" ", match.group(1)))
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                return title[:300]
    parsed = urlparse(url)
    filename = Path(unquote(parsed.path)).stem.replace("-", " ").replace("_", " ").strip()
    return (filename or parsed.hostname or "Web source")[:300]


async def download_web_source(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
    validate_network: bool = True,
) -> DownloadedWebSource:
    original_url = url.strip()
    current_url = original_url
    owns_client = client is None
    web_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=False,
        headers={
            "User-Agent": "Locus/1.0 (local knowledge wiki)",
            "Accept": "text/html,application/xhtml+xml,application/pdf,text/plain,text/csv,application/json;q=0.9,*/*;q=0.2",
        },
    )
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            if validate_network:
                await _require_public_url(current_url)
            try:
                async with web_client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        if redirect_count >= MAX_REDIRECTS:
                            raise WebIngestError("The URL redirected too many times", 502)
                        location = response.headers.get("location")
                        if not location:
                            raise WebIngestError("The URL returned an invalid redirect", 502)
                        current_url = urljoin(current_url, location)
                        continue
                    if response.is_error:
                        raise WebIngestError(
                            f"The website returned HTTP {response.status_code}", 502
                        )
                    extension, media_type = _content_kind(
                        response.headers.get("content-type", ""), current_url
                    )
                    declared_size = response.headers.get("content-length")
                    if declared_size and declared_size.isdigit() and int(declared_size) > MAX_DOWNLOAD_BYTES:
                        raise WebIngestError("Web sources are limited to 50 MB", 413)
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise WebIngestError("Web sources are limited to 50 MB", 413)
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    final_url = str(response.url)
                    return DownloadedWebSource(
                        original_url=original_url,
                        final_url=final_url,
                        content=content,
                        media_type=media_type,
                        extension=extension,
                        title=_source_title(
                            final_url, content, media_type, response.encoding
                        ),
                    )
            except WebIngestError:
                raise
            except httpx.HTTPError as error:
                raise WebIngestError("Could not download the website", 502) from error
    finally:
        if owns_client:
            await web_client.aclose()
    raise WebIngestError("The website could not be downloaded", 502)
