from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .archive_safety import MAX_DOCUMENT_BLOCKS, validate_zip_archive


def _serializable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def parse_xlsx(path: str | Path) -> list[dict]:
    validate_zip_archive(path)
    # read_only=False: merged_cells 정보 유지 + iter_rows는 셀을 새로 만들지 않음.
    # 압축 해제 폭탄은 validate_zip_archive, 셀 수는 아래 상한으로 방어.
    workbook = load_workbook(filename=str(path), data_only=True, read_only=False)
    blocks: list[dict] = []
    try:
        for sheet in workbook.worksheets:
            if sheet.max_row * sheet.max_column > 200_000:
                raise ValueError("셀 수 초과")
            merged = [str(cell_range) for cell_range in sheet.merged_cells.ranges]
            headers: list[Any] = []
            for row_number, row in enumerate(sheet.iter_rows(), start=1):
                if row_number == 1:
                    headers = [_serializable(cell.value) for cell in row]
                cells: list[dict[str, Any]] = []
                display: list[str] = []
                for column, cell in enumerate(row, start=1):
                    if cell.value is None:
                        continue
                    address = cell.coordinate
                    value = _serializable(cell.value)
                    cells.append(
                        {
                            "address": address,
                            "value": value,
                            "header": headers[column - 1] if row_number > 1 and column - 1 < len(headers) else None,
                            "data_type": cell.data_type,
                            "is_formula": False,
                            "calculated_value": value,
                        }
                    )
                    display.append(f"{address}={value}")
                if not cells:
                    continue
                if len(blocks) >= MAX_DOCUMENT_BLOCKS:
                    raise ValueError("문서 블록 수 초과")
                blocks.append(
                    {
                        "block_type": "spreadsheet_row",
                        "content": " | ".join(display),
                        "block_order": len(blocks),
                        "page_number": None,
                        "sheet_name": sheet.title,
                        "section_title": None,
                        "location_metadata": {
                            "row_number": row_number,
                            "cells": cells,
                            "headers": headers,
                            "merged_cells": merged,
                        },
                    }
                )
    finally:
        workbook.close()
    if not blocks:
        raise ValueError("XLSX에서 값이 있는 셀을 찾지 못했습니다")
    return blocks
