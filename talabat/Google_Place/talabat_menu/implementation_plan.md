# Fix Talabat Menu Scraping Issues

## Problem Analysis

After examining the logs and data, I found **3 critical issues**:

### Issue 1: Wrong Restaurants Returned (Query Mismatch)

The `queries` approach returns **wrong restaurants**. Evidence from the raw data:

| Input URL (slug) | Expected Restaurant | Actually Returned |
|---|---|---|
| `zam-zam-mandi-madinat-khalifa--a` | Zam Zam Mandi | **Johnny Rockets** |
| `zahrat-lebnan-mbz-city-tgo` | Zahrat Lebnan | **Arz Lebanon** |

The search query `"zam zam mandi madinat khalifa a"` matched `Johnny Rockets` instead. The actor's search is fuzzy -- it returns the "best match" from Talabat search, which often isn't the restaurant we want.

### Issue 2: 1 of 3 Restaurants Has No Menu

`Johnny Rockets` was returned with **no `menu_items`** at all (line 2 of CSV is empty for all item columns). This may be because:
- The wrong restaurant was matched (see Issue 1), and that restaurant's menu wasn't scraped
- Or the actor didn't enable menu scraping for search-result hits

### Issue 3: Progress File Blocks Re-runs

The [progress.json](file:///c:/Users/SagarSingh/Downloads/Google_Place/apify/talabat_menu/output/progress.json) records batch 1 as completed. When you re-ran with `--test 3`, log 3 shows:
```
Skipping batch 1/1 (already completed)
Total restaurants scraped : 0
```
The progress tracker doesn't distinguish between different run configurations (test vs full), so it skips batches that were already "done" even with different parameters.

---

## Root Cause

The **`queries`** input field uses Talabat's search engine, which is designed for consumers typing partial restaurant names -- it returns fuzzy best-matches, NOT exact matches. This is fundamentally unreliable for 1,000 specific restaurants.

The **`restaurantSlugs`** field requires brand-level slugs (e.g., `pizza-hut`) not branch-level slugs from URLs (e.g., `pizza-hut-al-barsha-1`). So the previous approach with slugs also didn't work (the 2nd log shows `0 restaurants` returned).

---

## Proposed Fix: Use `startUrls` Input

Looking at the actor's input schema, it accepts `startUrls` -- which takes **direct Talabat restaurant URLs**. This is the most reliable approach since we already have the exact URLs.

> [!IMPORTANT]
> This is a significant change to the scraping strategy. Instead of extracting slugs/queries from URLs, we'll pass the URLs directly to the actor via `startUrls`.

### Proposed Changes

---

#### [MODIFY] [run_talabat_menu.py](file:///c:/Users/SagarSingh/Downloads/Google_Place/apify/talabat_menu/run_talabat_menu.py)

1. **Change actor input to use `startUrls`** instead of `queries`:
   ```python
   actor_input = {
       "startUrls": [{"url": u} for u in batch_urls],
       "scrapeMenu": True,
       "scrapeMenuChoices": False,
   }
   ```
   Remove `queries`, `restaurantSlugs`, `country`, `maxResults` -- not needed when using direct URLs.

2. **Simplify batching** -- batch by URLs directly instead of generating queries/slugs:
   - Remove `slug_to_query()` function (no longer needed)
   - Remove query deduplication (URLs are already unique)
   - Batch the URLs directly

3. **Fix progress tracking** -- add a `--reset` flag and store run configuration hash so re-runs with different `--test` values don't conflict:
   ```python
   parser.add_argument("--reset", action="store_true", help="Clear progress and start fresh")
   ```

4. **Reduce default batch size** from 20 to 10 -- `startUrls` may be heavier per request since each URL needs individual page loading.

5. **Add URL-to-result matching** -- after scraping, log which input URLs didn't return results so we can identify failures.

---

#### [MODIFY] [process_menu_results.py](file:///c:/Users/SagarSingh/Downloads/Google_Place/apify/talabat_menu/process_menu_results.py)

Minor: The flattener should handle the `url` field from `startUrls` results (the returned `restaurant_url` format may differ slightly). No major changes expected.

---

#### [MODIFY] [test/test_slug_extraction.py](file:///c:/Users/SagarSingh/Downloads/Google_Place/apify/talabat_menu/test/test_slug_extraction.py)

- Update test imports (remove `slug_to_query` if deleted)
- Add tests for the URL batching logic
- Keep existing `extract_slug`, `extract_country`, and `load_urls` tests (these functions are still useful for metadata)

---

## Open Questions

> [!IMPORTANT]
> **Do you want me to first verify that `startUrls` works with the actor?** I can run a quick `--test 2` with just 2 URLs to confirm the actor accepts direct URLs and returns menus before modifying the full script. This will cost ~$0.004.

> [!NOTE]
> The existing `progress.json` has batch 1 marked as completed from the previous (incorrect) run. I'll reset it as part of the fix. The existing raw JSON file (`batch_001_*.json`) with the 3 wrong-match results will be preserved but won't be mixed into new results.

## Verification Plan

### Automated Tests
```bash
py -m pytest test/test_slug_extraction.py -v
```

### Manual Verification
1. Run `py run_talabat_menu.py --test 3 --reset` to test with 3 URLs
2. Verify the returned restaurant names match the input URLs
3. Verify menu items are populated (not empty)
4. Run `py process_menu_results.py` and check the output CSV
