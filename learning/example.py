from bs4 import BeautifulSoup
import requests
from requests import Response
import pandas as pd
import numpy as np


URL = "https://www.amazon.com/s?k=playstation+5&crid=WGYLDB8SF2SA&sprefix=playstat%2Caps%2C179&ref=nb_sb_ss_p13n-retrained-pltr-ranker_1_8"
HEADERS = ({ 'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1', 'accept-language': 'es-US,es-419;q=0.9,es;q=0.8' })

def requestToPage(url, headers) -> Response:
    webPage = requests.get(url, headers=headers)
    print(webPage.text)
    return webPage

def convertBeautifullSoup(content: bytes):
    soup = BeautifulSoup(content, 'html.parser')
    return soup

def searchLinks(soup: BeautifulSoup):
    linksHTML = soup.find_all("a", attrs={ 'class': 'a-link-normal s-line-clamp-2 s-link-style a-text-normal' })
    links = map( lambda link : f"https://amazon.com{link.get('href')}", linksHTML)
    return list(links)

def extractTitle(soup: BeautifulSoup): 
    title = soup.find("span", attrs={'id': 'productTitle'}).text.strip()
    return title

def extractPrice(soup: BeautifulSoup):
    price = soup.find("span", attrs={"class": "a-price-whole"})
    decimal = soup.find("span", attrs={"class": "a-price-fraction"})
    if (price and decimal): return f"{price.text.strip().replace('.', '')}.{decimal.text.strip()}"
    return ""

def extractReview(soup: BeautifulSoup):
    review = soup.find("div", attrs={"id": "averageCustomerReviews_feature_div"})
    rating = review.find("span", attrs={"id": "acrPopover"})
    return rating

def extractUrlImage(soup: BeautifulSoup):
    url = soup.find("div", attrs={"id": "imgTagWrapperId"})
    if (url):
        image = url.find("img")
        if (image):
            return image.get("src")
    return ""


def main():
    webPage = requestToPage(URL, HEADERS)
    soup = convertBeautifullSoup(webPage.content)
    linksProductsDetail = searchLinks(soup)
    d = { 'title': [], 'price': [],  'review': [], 'image': []}
    for url in linksProductsDetail[0:3]: 
        webPageProductDetail = requestToPage(url, HEADERS)
        soup = convertBeautifullSoup(webPageProductDetail.content)

        title = extractTitle(soup)
        price = extractPrice(soup)
        review = extractReview(soup)
        image = extractUrlImage(soup)

        d['title'].append(title)
        d['price'].append(price)
        d['review'].append(review)
        d['image'].append(image)

    

    amazon_df = pd.DataFrame.from_dict(d)
    amazon_df["title"] = amazon_df["title"].replace("", np.nan)
    amazon_df = amazon_df.dropna(subset=["title"])
    amazon_df.to_csv("csv/amazon_scrapping.csv", header=True, index=False)
