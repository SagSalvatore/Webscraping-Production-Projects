"""Try known Super Store Finder data paths."""
import asyncio
from curl_cffi.requests import AsyncSession

async def try_endpoints():
    base = "https://maisonlandemaine.com"
    
    # Known Super Store Finder paths
    endpoints = [
        "/wp-content/uploads/jeejee_ssf/storejson.txt",
        "/wp-content/uploads/jeejee_ssf/stores.xml",
        "/wp-content/uploads/jeejee_ssf/stores.json",
        "/wp-content/uploads/ssf/stores.xml",
        "/wp-content/uploads/ssf/storejson.txt",
        "/wp-content/plugins/superstorefinder-wp/ssf-wp-xml.php",
    ]
    
    async with AsyncSession(impersonate="chrome120") as session:
        for endpoint in endpoints:
            url = base + endpoint
            try:
                response = await session.get(url, timeout=10)
                status = response.status_code
                size = len(response.text)
                print(f"{status} - {size:6} bytes - {endpoint}")
                
                if status == 200 and size > 100:
                    print(f"  Content preview: {response.text[:200]}...")
                    
                    # Save if found
                    with open(f"France/output/landemaine_data.txt", "w", encoding="utf-8") as f:
                        f.write(response.text)
                    print("  Saved!")
                    return
            except Exception as e:
                print(f"Error - {endpoint}: {e}")

asyncio.run(try_endpoints())
