"""PDF 文件的本地私有存储。

服务只返回导入记录，不提供原文件下载接口。磁盘文件名由服务生成，客户端原文件名仅作为
元数据保存。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

from fastapi import UploadFile


class UnsupportedBookingDocumentError(Exception):
    """上传内容不是可接受的 PDF。"""


class BookingDocumentTooLargeError(Exception):
    """实际读取字节数超过服务上限。"""


@dataclass(frozen=True)
class StoredBookingDocument:
    """成功落盘后的非敏感元数据。"""

    storage_key: str
    path: Path
    size_bytes: int
    sha256: str
    original_filename: str
    content_type: str


class LocalBookingDocumentStorage:
    """将上传内容按随机名称写入 Git 忽略目录。"""

    _ALLOWED_CONTENT_TYPES: ClassVar[set[str]] = {
        "application/pdf",
        "application/octet-stream",
    }

    def __init__(self, root: str | Path, max_bytes: int) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes

    async def store(self, upload: UploadFile) -> StoredBookingDocument:
        """分块读取并复核真实大小和 PDF 文件头。"""

        original_filename = Path(upload.filename or "unnamed.pdf").name
        if not original_filename.lower().endswith(".pdf"):
            raise UnsupportedBookingDocumentError

        content_type = upload.content_type or "application/octet-stream"
        if content_type not in self._ALLOWED_CONTENT_TYPES:
            raise UnsupportedBookingDocumentError

        self.root.mkdir(parents=True, exist_ok=True)
        storage_key = f"{uuid4().hex}.pdf"
        target = (self.root / storage_key).resolve()
        if target.parent != self.root:
            raise UnsupportedBookingDocumentError
        temporary = target.with_suffix(".part")

        digest = hashlib.sha256()
        size_bytes = 0
        first_bytes = bytearray()

        try:
            with temporary.open("xb") as output:
                while chunk := await upload.read(64 * 1024):
                    size_bytes += len(chunk)
                    if size_bytes > self.max_bytes:
                        raise BookingDocumentTooLargeError
                    if len(first_bytes) < 5:
                        first_bytes.extend(chunk[: 5 - len(first_bytes)])
                    digest.update(chunk)
                    output.write(chunk)

            if size_bytes == 0 or bytes(first_bytes) != b"%PDF-":
                raise UnsupportedBookingDocumentError
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        return StoredBookingDocument(
            storage_key=storage_key,
            path=target,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
            original_filename=original_filename[:255],
            content_type="application/pdf",
        )

    def delete(self, storage_key: str) -> None:
        """仅删除本存储根目录中的随机键。"""

        target = (self.root / storage_key).resolve()
        if target.parent == self.root:
            target.unlink(missing_ok=True)

    def path_for(self, storage_key: str) -> Path:
        """解析内部随机键，供解析器读取。"""

        target = (self.root / storage_key).resolve()
        if target.parent != self.root:
            raise UnsupportedBookingDocumentError
        return target
