from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx

MAX_REDIRECTS = 5
CHUNK_SIZE = 1024 * 1024


class DownloadError(ValueError):
    pass


async def _require_public_https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DownloadError("Chỉ chấp nhận liên kết HTTPS tải trực tiếp.")
    if parsed.username or parsed.password:
        raise DownloadError("Liên kết không hợp lệ.")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise DownloadError("Không phân giải được máy chủ của liên kết.") from exc
    if not addresses:
        raise DownloadError("Không xác định được máy chủ của liên kết.")
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global:
            raise DownloadError("Liên kết trỏ tới địa chỉ mạng riêng, đã bị chặn.")


def _filename(response: httpx.Response, url: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    # Prefer RFC 5987 filename*=UTF-8''... when present.
    name = ""
    for part in disposition.split(";"):
        part = part.strip()
        if part.lower().startswith("filename*="):
            value = part.split("=", 1)[1].strip().strip('"')
            if "''" in value:
                name = unquote(value.split("''", 1)[1])
        elif part.lower().startswith("filename=") and not name:
            name = part.split("=", 1)[1].strip().strip('"')
    if not name:
        name = unquote(Path(urlparse(url).path).name)
    name = Path(name.replace("\\", "/")).name
    if not name or not name.lower().endswith(".zip"):
        raise DownloadError("Liên kết phải trỏ trực tiếp tới một file .zip.")
    return name[:180]


async def download_zip_from_url(url: str, destination: str | Path,
                                max_bytes: int = 500 * 1024 * 1024) -> tuple[str, int]:
    """Download a public HTTPS ZIP link safely, streaming to disk."""
    current = url.strip()
    timeout = httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = None
        for redirect_count in range(MAX_REDIRECTS + 1):
            await _require_public_https(current)
            try:
                response = await client.send(
                    client.build_request("GET", current, headers={"User-Agent": "Aki-AOV-Tools/1.0"}),
                    stream=True,
                )
            except httpx.HTTPError as exc:
                raise DownloadError(f"Không tải được liên kết ({type(exc).__name__}).") from exc
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                await response.aclose()
                if not location or redirect_count == MAX_REDIRECTS:
                    raise DownloadError("Liên kết chuyển hướng quá nhiều lần hoặc không hợp lệ.")
                current = urljoin(current, location)
                continue
            if response.status_code != 200:
                status = response.status_code
                await response.aclose()
                raise DownloadError(f"Máy chủ tải file trả về HTTP {status}.")
            break
        if response is None:
            raise DownloadError("Không nhận được phản hồi từ máy chủ.")

        try:
            name = _filename(response, current)
            length = response.headers.get("content-length")
            if length and int(length) > max_bytes:
                raise DownloadError(f"File vượt quá giới hạn {max_bytes // (1024 * 1024)} MB.")
            total = 0
            target = Path(destination)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as output:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    total += len(chunk)
                    if total > max_bytes:
                        raise DownloadError(f"File vượt quá giới hạn {max_bytes // (1024 * 1024)} MB.")
                    output.write(chunk)
            if total == 0:
                raise DownloadError("File tải về đang trống.")
            import zipfile
            if not zipfile.is_zipfile(target):
                raise DownloadError("Nội dung tải về không phải ZIP hợp lệ hoặc liên kết không phải link tải trực tiếp.")
            return name, total
        except Exception:
            Path(destination).unlink(missing_ok=True)
            raise
        finally:
            await response.aclose()
