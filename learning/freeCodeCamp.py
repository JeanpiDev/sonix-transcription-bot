from bs4 import BeautifulSoup
import requests

BASE_URL = 'https://scrapepark.org/courses/spanish/'

def getSourcePage(url):
    webPage = requests.get(url)
    print(webPage)
    return webPage.content

def soup(url):
    webPageContent = getSourcePage(url)
    soup = BeautifulSoup(webPageContent, "html.parser")
    return  soup

soupPage = soup(BASE_URL)

def showScreen(content):
    print(content, end="\n\n")

# Encontrar una etiqueta
showScreen(soupPage.find("h2"))

# Encontrar varias etiquetas con limite 
showScreen(soupPage.find_all("h2", limit=2))

# Usar get_text sobre la propiedad text 
showScreen([x.get_text(strip=True) for x in soupPage.find_all("h2")])

#Seleccionar por caracteristica 
filterElements = soupPage.find("section", attrs={'class': "why-section layout-padding"})
showScreen(filterElements.find("h2").get_text(strip=True))
for i in filterElements.find_all("div", class_="box"):
    showScreen(i.find("div", attrs={"class": "detail-box"}).get_text(strip=True))

# Descargar imagenes y extraer información
# images = soupPage.find_all("img", src=True)
# getUrlImages = [getSourcePage(f"{BASE_URL}/{x.get('src')}") for x in images if x.get('src').endswith(".png")]

# for i, image in enumerate(getUrlImages):
#     with open(f"./learning/images/{i}.png", "wb") as file:
#         file.write(image)

# Extraer un iframe

iframeElement = soupPage.find("iframe", attrs={"title": "table_iframe"})
iframeElementSrc = iframeElement.get("src")

soupTablePage = soup(f"{BASE_URL}/{iframeElementSrc}")
tableScrapper = soupTablePage.find("table")

# Obtener columnas de una tabla 
elementsTable = tableScrapper.find_all(["th", "td"], attrs={'style': 'color: red;'})
elementsTableText = [x.get_text(strip=True) for  x in elementsTable]
for i in elementsTableText:
    showScreen(i)

products = []
prices = []

# Obtener productos 
productosElement = soupPage.find("section", attrs={'class': 'product-section layout-padding'}).find_all("div", attrs={'class': 'col-sm-6 col-md-4 col-lg-4'})
for i in productosElement:
    title = i.find("h5").get_text(strip=True)
    products.append(title)
    price = i.find("h6").get_text(strip=True)
    prices.append(price)
    showScreen(f"title: {title}, price: {price}")

# Navegar por diferentes url 
navDropDown = soupPage.find_all("ul", attrs={'class': 'dropdown-menu'})[-1].find_all("a",  attrs={"class": "nav-link"})
links = [f"https://scrapepark.org{x.get('href')}" for x in navDropDown]
for url in links:
    soupContactDetail = soup(url).find("div", attrs={'class': 'footer-detail'}).find("h5").get_text(strip=True)
    showScreen(soupContactDetail)

# Uso de expresiones regulares 

import re

elementFind = soupPage.find(string=re.compile("MENÚ"))
print(elementFind.find_next())

wordsSearch = ["MENÚ", "patineta", "carpincho"]

for string in wordsSearch:
    try:
        elementFind = soupPage.find(string=re.compile(string))
        print(elementFind.text)
    except AttributeError as e:
        print(e)


# Uso de CSV para almacenar la información
products.insert(0, 'products')
prices.insert(0, 'prices')

datos = list(zip(products, prices))

import csv

with open('./learning/images/datos.csv', '+a', encoding="UTF-8") as file:
    w = csv.writer(file)
    w.writerows(datos)

    