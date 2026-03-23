from bs4 import BeautifulSoup

def analyze():
    with open("broadcom_dump.html", "r", encoding="utf-8") as f:
        html = f.read()
    
    soup = BeautifulSoup(html, "html.parser")
    
    # Try to find typical news item containers
    print("--- Searching for news items ---")
    
    # Check for recurring classes
    links = soup.find_all("a")
    print(f"Found {len(links)} links.")
    
    # Print first 10 links with text to see likely candidates
    count = 0
    for link in links:
        text = link.get_text(strip=True)
        href = link.get('href')
        if text and href and len(text) > 20: # Likely a headline
            print(f"Link: {text[:50]}... | Href: {href}")
            # print parent classes
            parent = link.parent
            print(f"  Parent: {parent.name}, Classes: {parent.get('class')}")
            count += 1
            if count > 10: break

if __name__ == "__main__":
    analyze()
