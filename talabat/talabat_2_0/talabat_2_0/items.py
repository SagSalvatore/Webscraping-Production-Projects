# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class Talabat20Item(scrapy.Item):
    # define the fields for your item here like:
    # name = scrapy.Field()
    pass


class TalabatRestaurantItem(scrapy.Item):
    """Item for restaurant basic information"""
    restaurant_id = scrapy.Field()
    name = scrapy.Field()
    cuisine_type = scrapy.Field()
    rating = scrapy.Field()
    delivery_time = scrapy.Field()
    delivery_fee = scrapy.Field()
    minimum_order = scrapy.Field()
    address = scrapy.Field()
    phone = scrapy.Field()
    image_url = scrapy.Field()
    url = scrapy.Field()
    area = scrapy.Field()
    latitude = scrapy.Field()
    longitude = scrapy.Field()
    basic_info_hash = scrapy.Field()
    menu_hash = scrapy.Field()
    content_hash = scrapy.Field()
    menu_items = scrapy.Field()
    scraped_at = scrapy.Field()


class TalabatMenuItemItem(scrapy.Item):
    """Item for individual menu items"""
    item_id = scrapy.Field()
    restaurant_id = scrapy.Field()
    name = scrapy.Field()
    description = scrapy.Field()
    price = scrapy.Field()
    category = scrapy.Field()
    image_url = scrapy.Field()
    available = scrapy.Field()
    options = scrapy.Field()  # For customization options
