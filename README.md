# Webscraping Production Projects

Production-focused scraping scripts for restaurant, hotel, menu, and marketplace data extraction across dynamic websites.

## Projects

| Website | Folder | Focus |
| --- | --- | --- |
| Booking.com | `booking.com/` | Hotel listing extraction, address parsing, Excel export workflow |
| Talabat | `talabat/` | Scrapy, requests, Selenium, validation, menu extraction, restaurant URL collection |
| Hungerstation | `Hungerstation/` | Restaurant discovery, menu extraction, JSON-LD parsing, Selenium and Playwright workflows |

## Tech Stack

- Python
- Scrapy
- Selenium
- Playwright
- Requests / aiohttp
- BeautifulSoup
- curl_cffi
- pandas / openpyxl
- JSON-LD extraction
- Proxy-ready workflows through environment variables
- Resume/checkpoint patterns for long scraping jobs

## Capability Highlights

- Building production scraping pipelines for JavaScript-heavy and anti-bot-protected websites
- Handling pagination, lazy loading, dynamic selectors, and restaurant/menu detail pages
- Extracting structured data from HTML, embedded JSON, and JSON-LD
- Designing retry, delay, rate-limit, checkpoint, and validation flows
- Keeping scraped datasets, logs, reports, credentials, and local files out of Git
- Organizing scripts by website and scraping method for reusable project delivery

## Security Notes

Credentials and API keys should be supplied through environment variables, never committed in code. Generated outputs such as CSV, JSON, Excel, logs, reports, screenshots, and browser state files are ignored by `.gitignore`.
