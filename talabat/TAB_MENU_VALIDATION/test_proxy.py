import requests
import random
from validate_menu import PROXY_USERNAME, PROXY_PASSWORD, PROXY_COUNTRY, create_proxy_session
from colorama import init, Fore

init(autoreset=True)

def test_proxy():
    print(f"{Fore.CYAN}🚀 Testing Proxy Connection...")
    print(f"User: {PROXY_USERNAME}")
    print(f"Country: {PROXY_COUNTRY}")
    
    try:
        session = create_proxy_session()
        
        # Test IP endpoint
        print(f"{Fore.YELLOW}⏳ Connecting to httpbin.org/ip...")
        response = session.get("https://httpbin.org/ip", timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"{Fore.GREEN}✅ SUCCESS! Proxy is working.")
            print(f"{Fore.GREEN}🌍 External IP: {data.get('origin')}")
        else:
            print(f"{Fore.RED}❌ Failed. Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"{Fore.RED}❌ Exception: {e}")

if __name__ == "__main__":
    test_proxy()
