"""Packing List 坐标解析预览 API。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.config import settings
from app.schemas.packing_list_import import PackingListPreview
from app.services.packing_list_imports import (
    PackingListDocumentTooLargeError,
    PackingListPreviewService,
    UnsupportedPackingListDocumentError,
)
from app.services.packing_list_parser import (
    CoordinatePackingListParser,
    UnreadablePackingListPdfError,
)

router = APIRouter(prefix="/packing-list-imports", tags=["packing-list-imports"])


def get_packing_list_preview_service() -> PackingListPreviewService:
    """组装无数据库依赖的内存预览服务。"""

    return PackingListPreviewService(
        CoordinatePackingListParser(),
        settings.packing_list_preview_max_bytes,
    )


ServiceDependency = Annotated[
    PackingListPreviewService,
    Depends(get_packing_list_preview_service),
]


@router.post("/preview", response_model=PackingListPreview)
async def preview_packing_list(
    file: Annotated[
        UploadFile,
        File(description="仅接受不超过限制、带文字层的 PDF"),
    ],
    service: ServiceDependency,
) -> PackingListPreview:
    """返回结构化候选；不持久化 PDF，也不返回 PDF 原文。"""

    try:
        return await service.preview(file)
    except PackingListDocumentTooLargeError as error:
        max_mib = settings.packing_list_preview_max_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"PDF 文件不能超过 {max_mib} MiB",
        ) from error
    except UnsupportedPackingListDocumentError as error:
        raise HTTPException(status_code=415, detail="只接受有效的 PDF 文件") from error
    except UnreadablePackingListPdfError as error:
        raise HTTPException(
            status_code=422,
            detail="PDF 已加密、损坏、空白或没有可识别的装载表格；当前版本不支持 OCR",
        ) from error
