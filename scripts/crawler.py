# lets build a python crawler that will scrape web for sinusitis documentation, we are going to use this for RAG application

# lets first start with requesting the website
import requests
from bs4 import BeautifulSoup
# lxml is a library for parsing xml and html
from lxml import etree
import re
# we are going to use only the government and health related websites in usa like cdc, fda, nih, etc.
URLS = [
    "https://www.cdc.gov",
    "https://www.fda.gov",
    "https://www.nih.gov",
    "https://medlineplus.gov",
    "https://www.mayoclinic.org",
    "https://www.webmd.com",
    "https://www.mayoclinic.org",
]

Keywords = [
    "sinusitis",
    "sinus infection",
    "sinusitis treatment",
    "sinusitis symptoms",
    "sinusitis causes",
    "sinusitis prevention",
    "sinusitis diagnosis",
]

def search_website_for_keywords(url):
    soup = crawl_website(url)
    for keyword in Keywords:
        if keyword in soup.get_text():
            return True
    return False
# we use sitemaps to crawl the website
def crawl_website(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    print(soup.get_text())
    return soup

def get_sitemap(url):
    soup = crawl_website(url + '/robots.txt')
    # print(soup.get_text())
    # this is just a txt file, we need to find the sitemap urls on this file
    sitemap_urls = []
    for line in soup.get_text().split('\n'):
        match = re.search(r'sitemap:\s*(.*)', line, re.IGNORECASE)
        if match:
            print('found sitemap: ' + match.group(1), match)
            # check if this is an array of urls
            if '[' in match.group(1) or ']' in match.group(1):
                sitemap_urls.extend(match.group(1).strip().split(','))
            else:
                sitemap_urls.append(match.group(1).strip())
    return sitemap_urls

def crawl_sitemap(url):
    soup = crawl_website(url)
    # now we save the sitemap urls and 
    return soup.get_text()

def get_website_content(url):
    soup = crawl_website(url)
    return soup.get_text()

def get_website_links(url):
    soup = crawl_website(url)
    return soup.find_all('a')

# print(get_website_content(URLS[0] + "/robots.txt"))
all_sitemaps = []
for url in URLS:
    sitemaps = get_sitemap(url)
    all_sitemaps.extend(sitemaps)

print(all_sitemaps)
