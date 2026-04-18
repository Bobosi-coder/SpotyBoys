from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

PARSER_DELTA_FILES = ["session_tracks.parquet", "session_meta.parquet", "love.parquet", "manifest.json"]


@dataclass(frozen=True)
class ParserExportContract:
    version: str
    output_files: List[str]
    row_counts: Dict[str, int]

    def validate(self) -> None:
        missing = set(PARSER_DELTA_FILES) - set(self.output_files)
        if missing:
            raise ValueError("parser export missing files: " + ", ".join(sorted(missing)))
