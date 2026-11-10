from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from pathlib import Path

from app.aov_merger import AOVMergeError, merge_aov_archives
from app.drive_import import DriveImportError, MAX_DRIVE_FILE_BYTES, download_drive_zip

MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILES = 10


def main() -> int:
    raw_links = os.environ.get("AKI_DRIVE_LINKS", "")
    links = [line.strip() for line in raw_links.splitlines() if line.strip()]
    if not 2 <= len(links) <= MAX_FILES:
        raise SystemExit("Cần từ 2 đến 10 link ZIP Drive: 1 ZIP nền và ít nhất 1 ZIP Vấn Tâm Kiếm.")

    with tempfile.TemporaryDirectory(prefix="aki-drive-aov-") as temp:
        workdir = Path(temp)
        input_paths: list[str] = []
        total = 0

        for index, link in enumerate(links, start=1):
            destination = workdir / f"input-{index:02d}.zip"
            try:
                name, size = download_drive_zip(link, str(destination))
            except DriveImportError as exc:
                raise SystemExit(f"Không tải được ZIP số {index}: {exc}") from exc

            if size > MAX_DRIVE_FILE_BYTES:
                raise SystemExit(f"ZIP số {index} vượt quá 500 MiB.")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise SystemExit("Tổng dung lượng ZIP đầu vào vượt quá 500 MiB.")
            input_paths.append(str(destination))
            print(f"Đã tải ZIP {index}/{len(links)}: {name} ({size / 1024 / 1024:.1f} MiB)")

        output = Path.cwd() / "Aki-AOV-10Skin-VanTamKiem.zip"
        try:
            report = merge_aov_archives(input_paths, str(output))
        except AOVMergeError as exc:
            raise SystemExit(f"Bộ ghép AOV từ chối dữ liệu: {exc}") from exc

        if not output.is_file() or output.stat().st_size == 0:
            raise SystemExit("Bộ ghép không tạo được ZIP kết quả.")
        if output.stat().st_size > 1024 * 1024 * 1024:
            output.unlink(missing_ok=True)
            raise SystemExit("ZIP kết quả vượt quá giới hạn 1 GiB.")

        with zipfile.ZipFile(output, "r") as archive:
            bad_member = archive.testzip()
        if bad_member:
            output.unlink(missing_ok=True)
            raise SystemExit(f"ZIP kết quả có mục lỗi CRC: {bad_member}")

        digest = hashlib.sha256()
        with output.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)

        size_mb = output.stat().st_size / 1024 / 1024
        sha256 = digest.hexdigest()
        print(f"Kết quả: {output.name} ({size_mb:.1f} MiB)")
        print(f"SHA-256: {sha256}")
        print(
            "Báo cáo engine: "
            f"base={report.get('base_version')} "
            f"VTK={report.get('vtk_version')} "
            f"AssetBundles={report.get('assetbundles')}"
        )

        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write("# Aki AOV merge\n\n")
                summary.write(f"- ZIP đầu vào: {len(links)}\n")
                summary.write(f"- Tổng ZIP đầu vào: {total / 1024 / 1024:.1f} MiB\n")
                summary.write(f"- ZIP kết quả: {output.name} ({size_mb:.1f} MiB)\n")
                summary.write(f"- SHA-256: `{sha256}`\n")
                summary.write(f"- Base: {report.get('base_version')}\n")
                summary.write(f"- VTK: {report.get('vtk_version')}\n")
                summary.write(f"- AssetBundles: {report.get('assetbundles')}\n")
                summary.write("\n> Đã kiểm tra CRC ZIP. Chưa thay thế thử nghiệm trong game iOS.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
