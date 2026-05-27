"""Product model matching: load CSV, auto-detect encoding, case-insensitive substring search."""

import csv
import logging

logger = logging.getLogger(__name__)


class ProductMatcher:
    """Load a product model list from CSV and match against message text."""

    def __init__(self, csv_path: str, case_sensitive: bool = False):
        self.models: list[str] = []
        self._case_sensitive = case_sensitive
        encoding = self._detect_encoding(csv_path)
        logger.info("Loading products from %s (encoding=%s)", csv_path, encoding)
        with open(csv_path, "r", encoding=encoding) as f:
            reader = csv.reader(f)
            next(reader)  # skip header row
            for row in reader:
                if row and row[0].strip():
                    model = row[0].strip()
                    if not case_sensitive:
                        model = model.lower()
                    self.models.append(model)
        logger.info("Loaded %d product models", len(self.models))

    def match(self, text: str) -> list[str]:
        """Return all models found as substrings in `text`.

        Matching is case-insensitive by default; set `case_sensitive=True`
        in config to change.
        """
        if not text:
            return []
        search_text = text if self._case_sensitive else text.lower()
        matched = [m for m in self.models if m in search_text]
        return matched

    @staticmethod
    def _detect_encoding(path: str) -> str:
        """Detect CSV encoding: BOM check → UTF-8 trial → GBK fallback."""
        with open(path, "rb") as f:
            head = f.read(4)
        # UTF-16 LE BOM
        if head[:2] == b"\xff\xfe":
            return "utf-16-le"
        # UTF-16 BE BOM
        if head[:2] == b"\xfe\xff":
            return "utf-16-be"
        # UTF-8 BOM
        if head[:3] == b"\xef\xbb\xbf":
            return "utf-8-sig"
        # Try reading as UTF-8
        try:
            with open(path, "r", encoding="utf-8") as f:
                f.read()
            return "utf-8"
        except UnicodeDecodeError:
            return "gbk"
