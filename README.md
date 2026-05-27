# Webscraping Production Projects

Production-focused scraping scripts for restaurant, hotel, menu, and marketplace data extraction across dynamic websites.

## Projects

| Platform | Website | Folder | Focus |
| --- | --- | --- | --- |
| <img src="assets/logos/booking.png" alt="Booking.com" width="120"> | Booking.com | `booking.com/` | Hotel listing extraction, address parsing, Excel export workflow |
| <img src="assets/logos/talabat.png" alt="Talabat" width="120"> | Talabat | `talabat/` | Scrapy, requests, Selenium, validation, menu extraction, restaurant URL collection |
| <img src="assets/logos/hungerstation.png" alt="Hungerstation" width="120"> | Hungerstation | `Hungerstation/` | Restaurant discovery, menu extraction, JSON-LD parsing, Selenium and Playwright workflows |
| <img src="assets/logos/zomato.png" alt="Zomato" width="120"> | Zomato | `Zomato/` | UAE restaurant listing extraction, menu image collection, image preparation, and multi-model vision OCR |
| <img src="assets/logos/gofood.png" alt="GoFood" width="120"> | GoFood Indonesia | `Gofood-Indonesia/` | Area-wise restaurant discovery with Playwright async scrolling and proxy-ready sessions |
| <img src="assets/logos/elmenus-cairo.png" alt="elmenus" width="120"> | elmenus Cairo | `elmenus-cairo/` | Hidden API/XHR extraction for Cairo listings with location-aware request parameters |
| <img src="assets/logos/foodi.png" alt="Foodi" width="120"> | Foodi Bangladesh | `foodi-Bangladesh/` | Lat-long driven branch scraping through API requests and browser-like headers |

## Tech Stack

- Python
- Scrapy
- Selenium
- Playwright
- Requests / aiohttp
- BeautifulSoup/Selectolax
- Hidden fetch/XHR API analysis
- Header rotation and browser-like request profiles
- Latitude/longitude area-wise scraping
- curl_cffi
- pandas / openpyxl
- JSON-LD extraction
- Vision OCR with OpenAI, Groq, and Mistral
- Proxy-ready workflows through environment variables
- Resume/checkpoint patterns for long scraping jobs

## Capability Highlights

- Building production scraping pipelines for JavaScript-heavy and anti-bot-protected websites
- Handling pagination, lazy loading, dynamic selectors, and restaurant/menu detail pages
- Reverse-engineering hidden fetch/XHR endpoints and replaying them with correct headers, auth context, and lat-long parameters
- Extracting structured data from HTML, embedded JSON, and JSON-LD
- Building complete menu pipelines: scrape UAE Zomato listings, collect menu images, prepare images for OCR, and extract menu text using OpenAI, Groq LLaMA-4 Scout, and Mistral Pixtral vision models
- Designing retry, delay, rate-limit, checkpoint, and validation flows
- Keeping scraped datasets, logs, reports, credentials, and local files out of Git
- Organizing scripts by website and scraping method for reusable project delivery

## Security Notes

Credentials and API keys should be supplied through environment variables, never committed in code. Generated outputs such as CSV, JSON, Excel, logs, reports, screenshots, and browser state files are ignored by `.gitignore`.
