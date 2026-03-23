from bs4 import BeautifulSoup

def analyze():
    with open("dump_veeam.html", "r", encoding="utf-8") as f:
        html = f.read()
    
    soup = BeautifulSoup(html, "html.parser")
    
    # Try to find typical news item containers
    print("--- Searching for blog items ---")
    
    # Check for common container classes or tags
    articles = soup.find_all("article")
    print(f"Found {len(articles)} <article> tags.")
    
    if articles:
        for i, article in enumerate(articles[:3]):
            print(f"\nArticle {i+1}:")
            print(f"  Classes: {article.get('class')}")
            # print first link
            link = article.find('a')
            if link:
                print(f"  First Link: {link.get_text(strip=True)} -> {link.get('href')}")
            
            # check for h1-h6
            for h in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                header = article.find(h)
                if header:
                    print(f"  Header <{h}>: {header.get_text(strip=True)}")

    # if no articles, search for divs with specific likely classes
    else:
        print("No articles found. Searching for divs with 'blog', 'post', 'card'...")
        for cls in ['blog', 'post', 'card']:
            divs = soup.find_all("div", class_=lambda c: c and cls in c)
            print(f"Found {len(divs)} divs containing '{cls}'.")
            if divs:
                 print(f"Sample class: {divs[0].get('class')}")

if __name__ == "__main__":
    analyze()
