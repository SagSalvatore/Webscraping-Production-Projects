import scrapy


class RestaurantBranchItem(scrapy.Item):
    # --- URL ---
    url = scrapy.Field()             # Full URL: /uae/restaurant/{branch_id}/{slug}?aid={area_id}

    # --- Talabat IDs (IMPORTANT: keep these separate) ---
    branch_id = scrapy.Field()       # branchId — unique per physical location (used in URL)
    restaurant_id = scrapy.Field()   # restaurantId/id — parent chain (same across branches)

    branch_slug = scrapy.Field()     # e.g. "krave-difc"
    name = scrapy.Field()            # restaurant display name

    # --- Geo (critical for cross-platform matching) ---
    lat = scrapy.Field()             # latitude from Talabat vendor data
    lon = scrapy.Field()             # longitude from Talabat vendor data

    # --- Area context ---
    area_name = scrapy.Field()       # e.g. "Dubai World Trade Center - DWTC"
    area_id = scrapy.Field()         # int, extracted from listing URL

    # --- Scrape metadata ---
    page_found = scrapy.Field()      # which page number this branch appeared on
    scraped_at = scrapy.Field()      # ISO UTC timestamp
