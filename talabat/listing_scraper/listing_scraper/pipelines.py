import json
import os
from collections import defaultdict
from datetime import datetime, timezone


class DedupeRestaurantUrlPipeline:
    """
    Drop duplicate branches across areas.

    WHY branch_id (not restaurant_id):
      branch_id = unique physical location (the number in the URL).
      restaurant_id = parent chain — one chain has many branches.
      The same branch can appear in multiple area listing pages
      (overlapping delivery zones), so we dedup by branch_id.
    """

    def open_spider(self, spider):
        self._seen: set[int] = set()
        self._total = 0
        self._dropped = 0

    def process_item(self, item, spider):
        from scrapy.exceptions import DropItem
        bid = item.get("branch_id")
        self._total += 1
        if bid and bid in self._seen:
            self._dropped += 1
            raise DropItem(f"Duplicate branch_id={bid}")
        if bid:
            self._seen.add(bid)
        return item

    def close_spider(self, spider):
        unique = self._total - self._dropped
        spider.logger.info(
            f"[Dedup] total seen={self._total} | "
            f"duplicates dropped={self._dropped} | unique={unique}"
        )


class JsonLinesPipeline:
    """
    Appends each unique branch as a JSON line to the output file.
    Also maintains:
      - area_progress.json  : per-area branch counts (autosaved every 500 items)
      - talabat_restaurant_urls_summary.json : final summary at spider close
    """

    AUTOSAVE_EVERY = 500  # flush area_progress every N items

    def open_spider(self, spider):
        output_path = spider.settings.get("RESTAURANT_URLS_OUTPUT")
        self._output_path = output_path
        self._progress_path = spider.settings.get("AREA_PROGRESS_OUTPUT")
        self._summary_path = spider.settings.get("SUMMARY_OUTPUT")

        self._file = open(output_path, "a", encoding="utf-8")
        self._count = 0
        self._area_counts: dict = defaultdict(int)  # area_name → unique branch count
        self._started_at = _now_iso()

        spider.logger.info(f"Writing restaurant branches → {output_path}")
        spider.logger.info(f"Area progress → {self._progress_path}")

    def process_item(self, item, spider):
        line = json.dumps(dict(item), ensure_ascii=False)
        self._file.write(line + "\n")
        self._count += 1
        self._area_counts[item.get("area_name", "unknown")] += 1

        if self._count % self.AUTOSAVE_EVERY == 0:
            self._file.flush()
            self._save_progress()
            spider.logger.info(
                f"[Progress] {self._count} unique branches written | "
                f"{len(self._area_counts)} areas touched"
            )
        return item

    def close_spider(self, spider):
        self._file.close()
        self._save_progress()
        self._save_summary()
        spider.logger.info(
            f"[Done] {self._count} unique branches across "
            f"{len(self._area_counts)} areas"
        )

    def _save_progress(self):
        data = {
            "last_saved": _now_iso(),
            "total_unique_branches": self._count,
            "areas_started": len(self._area_counts),
            "per_area": dict(self._area_counts),
        }
        _write_json(self._progress_path, data)

    def _save_summary(self):
        summary = {
            "platform": "talabat",
            "country": "uae",
            "started_at": self._started_at,
            "completed_at": _now_iso(),
            "total_unique_branches": self._count,
            "areas_covered": len(self._area_counts),
            "output_file": self._output_path,
        }
        _write_json(self._summary_path, summary)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: str, data: dict):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)  # atomic write — no partial file on crash
