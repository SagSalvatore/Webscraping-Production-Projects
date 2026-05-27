import requests
from scrapy import Selector
import json

def test_next_data_extraction():
    url = 'https://www.talabat.com/uae/restaurant/749515/wimpy-mohammed-bin-zayed-city?aid=6484'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    print(f"Testing URL: {url}")
    
    try:
        r = requests.get(url, headers=headers)
        print(f"Status Code: {r.status_code}")
        
        if r.status_code == 200:
            sel = Selector(text=r.text)
            
            # Try different ways to find __NEXT_DATA__
            script = sel.xpath('//script[@id="__NEXT_DATA__"]/text()').get()
            print(f"Found __NEXT_DATA__ with XPath: {bool(script)}")
            
            if not script:
                script = sel.css('script#__NEXT_DATA__::text').get()
                print(f"Found __NEXT_DATA__ with CSS: {bool(script)}")
            
            if not script:
                # Look for any script containing __NEXT_DATA__
                scripts = sel.xpath('//script/text()').getall()
                for s in scripts:
                    if '__NEXT_DATA__' in s:
                        script = s
                        print("Found __NEXT_DATA__ in script content")
                        break
            
            if script:
                print(f"Script length: {len(script)}")
                try:
                    data = json.loads(script)
                    print("JSON parsing successful")
                    print(f"Top-level keys: {list(data.keys())}")
                    
                    # Try to find restaurant data
                    props = data.get('props', {})
                    page_props = props.get('pageProps', {})
                    print(f"PageProps keys: {list(page_props.keys())}")
                    
                except json.JSONDecodeError as e:
                    print(f"JSON parsing failed: {e}")
            else:
                print("No __NEXT_DATA__ script found")
                # Check if page has any scripts
                all_scripts = sel.xpath('//script').getall()
                print(f"Total scripts found: {len(all_scripts)}")
        else:
            print(f"Request failed with status: {r.status_code}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_next_data_extraction()