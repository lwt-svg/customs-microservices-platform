"""Packing List 上传预览服务；文件只在内存中短暂存在。"""

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.schemas.packing_list_import import PackingListPreview
from app.services.packing_list_parser import CoordinatePackingListParser


class UnsupportedPackingListDocumentError(Exception):
    """上传文件不是同时满足扩展名、MIME 和 magic bytes 的 PDF。"""


class PackingListDocumentTooLargeError(Exception):
    """上传文件超过预览接口上限。"""


class PackingListPreviewService:
    """校验上传边界，并在线程池执行同步 PDF 解析。"""

    def __init__(
        self,
        parser: CoordinatePackingListParser,
        max_bytes: int,
    ) -> None:
        self.parser = parser
        self.max_bytes = max_bytes

    async def preview(self, upload: UploadFile) -> PackingListPreview:
        """读取不超过上限的 PDF；不落盘，也不保存全文或文件名。"""

        filename = (upload.filename or "").casefold()
        content_type = (upload.content_type or "").split(";", maxsplit=1)[0].casefold()
        if not filename.endswith(".pdf") or content_type != "application/pdf":
            raise UnsupportedPackingListDocumentError

        try:
            content = await upload.read(self.max_bytes + 1)
        finally:
            await upload.close()
        if len(content) > self.max_bytes:
            raise PackingListDocumentTooLargeError
        if not content.startswith(b"%PDF-"):
            raise UnsupportedPackingListDocumentError

        return await run_in_threadpool(self.parser.parse_bytes, content)
