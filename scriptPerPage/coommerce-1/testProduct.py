from bs4 import BeautifulSoup
import requests
import time
from kit_tools import connect_webdriver, desconectedWebDriver, ShorHandsSeleniumSelectors, Products
import re

BASE_URL = 'https://bluesheep.com.ar/15030712pcl.html#'

webdriver = connect_webdriver(BASE_URL)
kit_tools = ShorHandsSeleniumSelectors(webdriver)

element1 = kit_tools.selectById('custom-2')
element1.click()



time.sleep(2)

soupDetailProduct = BeautifulSoup(kit_tools.getHtmlCode(), "html.parser")

        
title = soupDetailProduct.find("h1", attrs={'class': 'page-title'}).get_text()
complete_sku = soupDetailProduct.find("span", attrs={'id': 'dynamic-data-sku'}).get_text()
large_description = soupDetailProduct.find("span", attrs={'id': 'dynamic-data-description'}).get_text()
composition = soupDetailProduct.find("span", attrs={'id': 'dynamic-data-composition'}).get_text()
care_dress = soupDetailProduct.find("span", attrs={'id': 'dynamic-data-clothes_care'}).get_text()
list_prices = soupDetailProduct.find("span", {'class': 'price-wrapper price-including-tax'}).get_text()
lower_price = soupDetailProduct.find("span", {'class': 'price-wrapper price-excluding-tax'}).get_text()
colors = soupDetailProduct.find("div", attrs={'id': 'swatch-group-93'}).find_all('div', attrs={'attribute-id': '93'})
talles = soupDetailProduct.find('div', attrs={'id': 'swatch-group-137'}).find_all('div', attrs={'attribute-id': '137'})
materials = re.findall(r'\d+% (\D+)', composition)
text_materials = ", ".join(materials)

print(title)
print(complete_sku)
print(large_description)
print(text_materials)
print(composition)
print(care_dress)
print(list_prices)
print(lower_price)
print(len(colors))
print(len(talles))

products = []
skus = []

for color in colors:
    id_selector = color.get('id')
    kit_tools.selectById(id_selector).click()
    text_color = BeautifulSoup(kit_tools.getHtmlCode(), "html.parser").find("span", attrs={'class': 'swatch-attribute-selected-option'}).get_text()
    print(text_color)
    kit_tools.selectById('custom-1').click()
    imagesUrl = []
    for talle in talles:
        id_selector_talle = talle.get('id')
        kit_tools.selectById(id_selector_talle).click()
        kit_tools.selectById('custom-1').click()
        text_talle = BeautifulSoup(kit_tools.getHtmlCode(), "html.parser").find("span", attrs={'id': 'swatch-group-selected-137'}).get_text()
        sku = BeautifulSoup(kit_tools.getHtmlCode(), "html.parser").find("span", attrs={'id': 'dynamic-data-sku'}).get_text()
        sku_base = sku[0:9].strip()
        sku_variant = sku.replace(sku_base, "").strip()
        imagesSoup = BeautifulSoup(kit_tools.getHtmlCode(), "html.parser").find_all("picture")
        for i in imagesSoup:
            source = i.find("img").get("src")
            if (len(source) > 0 and source not in imagesUrl ):
                imagesUrl.append(source)

        product_instance = Products(sku_base, sku, title, "", large_description, "", text_talle, text_color, sku_variant, "", "F", "", text_materials, "", "ARG", "", "", "", composition, "", care_dress, "", "", float(list_prices.replace("$", "").replace(".", "").replace(",", ".")), float(lower_price.replace("$", "").replace(".", "").replace(",", ".")), "BLUE SHEEP", imagesUrl, BASE_URL)

        products.append(product_instance)


sku_base = ['SKU BASE']
sku_complete = ['SKU']
nombre_producto = ['NOMBRE DEL PRODUCTO']
estilo = ['ESTILO']
categoria = ['CATEGORIA']
subcategory = ['SUBCATEGORIA']
description_large = ['DESCRIPCION LARGA']
description_corta = ['DESCRIPCION CORTA']
talle = ['TALLE']
color = ['COLOR']
varian_sku = ['VARIANT SKU']
stock = ['STOCK']
genero = ['GENERO']
designer = ['DISEÑADOR']
material = ['MATERIAL']
hertage = ['HERTAGE']
origen = ['ORIGEN']
uses = ['USO']
net_weight = ['PESO NETO (KGS)']
composition_pecent = ['COMPOSICIÓN TEXTIL ( EJ. ALGODÓN 80% + RAYON VISCOSA 20%)']
tejido = ['TEJIDO (EJ. PUNTO O PLANO)']
care = ['CUIDADO (EJ. MACHINE, DRY CLEAN, ETC)']
seo = ['SEO KEYWORDS']
list_price = ['PRECIO LISTA']
buy_price = ['PRECIO DE VENTA']
brand = ['MARCA']
imagen1 = ['IMAGEN 1']
imagen2 = ['IMAGEN 2']
imagen3 = ['IMAGEN 3']
imagen4 = ['IMAGEN 4']
imagen5 = ['IMAGEN 5']
url_scrapping = ['URL SCRAPPING']

def formatCSV(text):
    if (text == None):
        return ""
    if (type(text) == float):
        return text
    if (len(text) == 0 ):
        return ""
    if (text):
        return text
    return ""


for i in products:
    product: Products = i
    sku_base.append(formatCSV(product.sku_base))
    sku_complete.append(formatCSV(product.sku_complete))
    nombre_producto.append(formatCSV(product.name_product))
    estilo.append(formatCSV(product.style))
    categoria.append(formatCSV(product.category))
    subcategory.append(formatCSV(product.subcategory))
    description_large.append(formatCSV(product.description_large))
    description_corta.append(formatCSV(product.description_short))
    talle.append(formatCSV(product.talle))
    color.append(formatCSV(product.color))
    varian_sku.append(formatCSV(product.varian_sku))
    stock.append(formatCSV(product.stock))
    genero.append(formatCSV(product.genere))
    designer.append(formatCSV(product.designer))
    material.append(formatCSV(product.material))
    hertage.append(formatCSV(product.heritage))
    origen.append(formatCSV(product.origin))
    uses.append(formatCSV(product.use))
    net_weight.append(formatCSV(product.weight_max))
    composition_pecent.append(formatCSV(product.composition_percent))
    tejido.append(formatCSV(product.points_dress))
    care.append(formatCSV(product.care))
    seo.append(formatCSV(product.seowords))
    list_price.append(formatCSV(product.price_list))
    buy_price.append(formatCSV(product.price_lower))
    brand.append(formatCSV(product.brand))
    imagen1.append(formatCSV(product.images_list[0] if len(product.images_list) > 0 else ""))
    imagen2.append(formatCSV(product.images_list[1] if len(product.images_list) > 1 else ""))
    imagen3.append(formatCSV(product.images_list[2] if len(product.images_list) > 2 else ""))
    imagen4.append(formatCSV(product.images_list[3] if len(product.images_list) > 3 else ""))
    imagen5.append(formatCSV(product.images_list[4] if len(product.images_list) > 4 else ""))
    url_scrapping.append(formatCSV(product.url))


# Create the zip of the lists
datos = list(zip(sku_base, sku_complete, nombre_producto, estilo, categoria, subcategory, description_large, description_corta, talle, color, varian_sku, stock, genero, designer, material, hertage, origen, uses, net_weight, composition_pecent, tejido, care, seo, list_price, buy_price, brand, imagen1, imagen2, imagen3, imagen4, imagen5, url_scrapping))

# Write the data to a CSV file
import csv

with open('./csv/datos.csv', 'w', encoding="utf-8-sig") as file:
    w = csv.writer(file)
    w.writerows(datos)


time.sleep(2)
desconectedWebDriver(webdriver)