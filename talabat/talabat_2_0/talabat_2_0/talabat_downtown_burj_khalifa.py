import scrapy
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse


class TalabatDowntownBurjKhalifaSpider(scrapy.Spider):
    name = "talabat_downtown_burj_khalifa"
    allowed_domains = ["www.talabat.com"]
    
    def __init__(self):
        self.start_urls = ["https://www.talabat.com/uae/restaurants/1258/downtown-burj-khalifa"]
        self.page_count = 0
        self.max_pages = 218  # Updated to scrape all 218 pages

    def start_requests(self):
        for url in self.start_urls:
            yield scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        # Extract restaurant URLs from the current page
        # Try multiple selectors to ensure we capture all links
        
        # Selector 1: More general selector for restaurant cards
        restaurant_links = response.css('a[href*="/restaurant/"]::attr(href)').getall()
        
        # Selector 2: Based on the user-provided XPath
        if not restaurant_links:
            restaurant_links = response.xpath('/html/body/div[1]/div/div[1]/div/div/div[2]/div[2]/div[2]/div/a/@href').getall()
            
        # Selector 3: Alternative XPath selector
        if not restaurant_links:
            restaurant_links = response.xpath('//*[@id="__next"]//a[contains(@href, "/restaurant/")]/@href').getall()
        
        # Remove duplicates while preserving order
        seen = set()
        unique_links = []
        for link in restaurant_links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)
        
        self.logger.info(f"Found {len(unique_links)} restaurant links on page {response.url}")
        
        # Yield each restaurant URL
        for link in unique_links:
            # Ensure the link is complete
            if link.startswith('/'):
                link = response.urljoin(link)
            yield {
                'url': link
            }
        
        # Handle pagination by incrementing the page parameter
        self.page_count += 1
        self.logger.info(f"Current page count: {self.page_count}")
        
        if self.page_count < self.max_pages:
            # Get current URL and parse it
            parsed_url = urlparse(response.url)
            query_params = parse_qs(parsed_url.query)
            
            # Get current page number or default to 1
            current_page = int(query_params.get('page', [1])[0])
            next_page = current_page + 1
            
            # Update the page parameter
            query_params['page'] = [str(next_page)]
            
            # Reconstruct the URL with the new page parameter
            new_query = urlencode(query_params, doseq=True)
            next_url = urlunparse((
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                parsed_url.fragment
            ))
            
            self.logger.info(f"Following to next page: {next_url} (Page {next_page} of {self.max_pages})")
            yield response.follow(next_url, self.parse)
        else:
            self.logger.info("Maximum page limit reached, ending crawl")