from bs4 import BeautifulSoup

def verify():
    with open("broadcom_dump.html", "r", encoding="utf-8") as f:
        html = f.read()
    
    soup = BeautifulSoup(html, "html.parser")
    
    # Proposed selector
    selector = "div.post-grid-item-v2__text, div.post-featured-v2__text"
    
    items = soup.select(selector)
    print(f"Selector '{selector}' found {len(items)} items.")
    
    for i, item in enumerate(items[:5]):
        links = item.find_all('a')
        print(f"Item {i+1} has {len(links)} links:")
        for link in links:
            print(f"  - Text: '{link.get_text(strip=True)[:20]}...' | Parent: <{link.parent.name}>")

if __name__ == "__main__":
    verify()
