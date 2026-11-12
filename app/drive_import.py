from __future__ import annotations

import http.cookiejar
import ipaddress
import re
import socket
import urllib.parse
import urllib.request
import zipfile
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path

MAX_DRIVE_FILE_BYTES = 500 * 1024 * 1024
_ALLOWED_HOSTS = {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}
_ALLOWED_SUFFIXES = (".googleusercontent.com",)
_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


class DriveImportError(ValueError):
    pass


def _allowed_google_host(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in _ALLOWED_HOSTS or any(host.endswith(suffix) for suffix in _ALLOWED_SUFFIXES)


def _validate_google_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not _allowed_google_host(parsed.hostname or ""):
        raise DriveImportError("Google Drive chuyển hướng tới địa chỉ không được phép.")
    host = parsed.hostname
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise DriveImportError("Không phân giải được máy chủ Google Drive.") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DriveImportError("Địa chỉ máy chủ tải xuống không an toàn.")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_google_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _DriveFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms: list[tuple[str, dict[str, str]]] = []
        self._action: str | None = None
        self._fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._action = attrs.get("action", "")
            self._fields = {}
        elif tag == "input" and self._action is not None:
            name = attrs.get("name")
            if name:
                self._fields[name] = attrs.get("value", "")
        elif tag == "a":
            href = attrs.get("href", "")
            if "confirm=" in href:
                self.forms.append((href, {}))

    def handle_endtag(self, tag):
        if tag == "form" and self._action is not None:
            self.forms.append((self._action, dict(self._fields)))
            self._action = None
            self._fields = {}


def _extract_file_id(shared_url: str) -> str:
    parsed = urllib.parse.urlparse(shared_url.strip())
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {"drive.google.com", "docs.google.com"}:
        raise DriveImportError("Chỉ chấp nhận link chia sẻ drive.google.com hoặc docs.google.com.")
    query = urllib.parse.parse_qs(parsed.query)
    file_id = (query.get("id") or [""])[0]
    if not file_id:
        match = re.search(r"/file/d/([^/]+)", parsed.path)
        if match:
            file_id = match.group(1)
    if not _FILE_ID.fullmatch(file_id):
        raise DriveImportError("Không đọc được mã file từ link Google Drive.")
    return file_id


def _filename_from_headers(headers, fallback: str) -> str:
    value = headers.get("Content-Disposition", "")
    message = Message()
    message["content-disposition"] = value
    name = message.get_filename()
    if name:
        name = Path(name.replace("\\", "/")).name
        if name.lower().endswith(".zip"):
            return name
    return fallback


def download_drive_zip(shared_url: str, destination: str) -> tuple[str, int]:
    """Download one public Google Drive ZIP without accepting arbitrary remote URLs."""
    file_id = _extract_file_id(shared_url)
    initial_url = "https://drive.google.com/uc?export=download&id=" + urllib.parse.quote(file_id)
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies),
        _SafeRedirect(),
    )

    def request(url: str):
        _validate_google_url(url)
        return opener.open(urllib.request.Request(url, headers={"User-Agent": "Aki-AOV-Tools/1.0"}), timeout=45)

    try:
        response = request(initial_url)
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type:
            html = response.read(1024 * 1024 + 1)
            response.close()
            if len(html) > 1024 * 1024:
                raise DriveImportError("Trang xác nhận Google Drive quá lớn.")
            parser = _DriveFormParser()
            parser.feed(html.decode("utf-8", errors="replace"))
            candidates = []
            for action, fields in parser.forms:
                target = urllib.parse.urljoin(initial_url, action)
                if fields:
                    parts = urllib.parse.urlparse(target)
                    merged = urllib.parse.parse_qs(parts.query)
                    merged.update({key: [value] for key, value in fields.items()})
                    target = urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(merged, doseq=True)))
                candidates.append(target)
            if not candidates:
                for cookie in cookies:
                    if cookie.name.startswith("download_warning"):
                        candidates.append(initial_url + "&confirm=" + urllib.parse.quote(cookie.value))
                        break
            if not candidates:
                raise DriveImportError("Google Drive không cung cấp nút xác nhận tải xuống. Hãy bật quyền 'Bất kỳ ai có đường liên kết'.")
            response = request(candidates[0])
            content_type = (response.headers.get("Content-Type") or "").lower()

        if "text/html" in content_type:
            response.close()
            raise DriveImportError("Google Drive vẫn trả trang xác nhận thay vì ZIP. Kiểm tra quyền chia sẻ của file.")
        name = _filename_from_headers(response.headers, f"drive-{file_id}.zip")
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DRIVE_FILE_BYTES:
            response.close()
            raise DriveImportError("File Google Drive vượt quá giới hạn 500 MB.")
        total = 0
        with open(destination, "wb") as target:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DRIVE_FILE_BYTES:
                    raise DriveImportError("File Google Drive vượt quá giới hạn 500 MB.")
                target.write(chunk)
        response.close()
        if total == 0 or not zipfile.is_zipfile(destination):
            Path(destination).unlink(missing_ok=True)
            raise DriveImportError("Nội dung tải về không phải ZIP hợp lệ. Có thể link không công khai hoặc không trỏ tới file ZIP.")
        return name, total
    except DriveImportError:
        Path(destination).unlink(missing_ok=True)
        raise
    except Exception as exc:
        Path(destination).unlink(missing_ok=True)
        raise DriveImportError(f"Tải Google Drive thất bại: {type(exc).__name__}.") from exc
