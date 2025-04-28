from selenium.webdriver.chrome.service import Service
from selenium.webdriver import Chrome, ChromeOptions
from webdriver_manager.chrome import ChromeDriverManager

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as wait 
from selenium.webdriver.common.by import By

from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement

from bs4 import BeautifulSoup 
import time

def connect_webdriver(url):
    service = Service(ChromeDriverManager().install())
    options = ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    driver = Chrome(service=service, options=options)
    driver.get(url)
    return driver

def desconectedWebDriver(webdriver: WebDriver):
    time.sleep(5)
    webdriver.quit()

class ShorHandsSeleniumSelectors:
    def __init__(self, webdriver: WebDriver):
        self.__webdriver = webdriver
    
    def selectBy(self, keyProp: str, value: str) -> WebElement:
        """
        Con este metodo puedes seleccionar meta datos
        data-test
        data-name
        """
        print(f"//*[@{keyProp}='{value}']")
        element = self.__webdriver.find_element(By.XPATH, f"//*[@{keyProp}='{value}']")
        return element
    
    def selectById(self, value):
        element = self.__webdriver.find_element(By.ID, value)
        return element
    
    def selectByName(self, value):
        element = self.__webdriver.find_element(By.NAME, value)
        return element

    def selectTagByProperties(self, tag, properties, elementSelect):
        element = self.__webdriver.find_element(By.XPATH, f"//{tag}[@{properties['a']}='{properties['v']}']/{elementSelect}")
        return element
    
    def selectByClass(self, tag, value):
        element = self.__webdriver.find_element(By.XPATH, f"//{tag}[@class='{value}']")
        return element

    def getHtmlCode(self):
        return self.__webdriver.page_source

    def isClicable(self, element: WebElement):
        """
        Este metodo es para esperar a que este visible el elemento antes de hacer click
        """
        result = wait(self.__webdriver, 2).until(EC.element_to_be_clickable(element))
        result.click()



class Products:
    def __init__(self, sku_base=None, sku_complete=None, name_product=None, style=None, description_large=None, description_short=None, talle=None, color=None, varian_sku=None, stock=None, genere=None, designer=None, material=None, heritage=None, origin=None, use=None, weight_max=None, weight_min=None, composition_percent=None, points_dress=None, care=None, seowords=None, meassures_body=None, price_list=None, price_lower=None, brand=None, images_list=None):
        self.sku_base = sku_base
        self.sku_complete = sku_complete
        self.name_product = name_product
        self.style = style
        self.description_large = description_large
        self.description_short = description_short
        self.talle = talle
        self.color = color
        self.varian_sku = varian_sku
        self.stock = stock
        self.genere = genere
        self.designer = designer
        self.material = material
        self.heritage = heritage
        self.origin = origin
        self.use = use
        self.weight_max = weight_max
        self.weight_min = weight_min
        self.composition_percent = composition_percent
        self.points_dress = points_dress
        self.care = care
        self.seowords = seowords
        self.meassures_body = meassures_body
        self.price_list = price_list
        self.price_lower = price_lower
        self.brand = brand
        self.images_list = images_list

    def set_attribute(self, attribute_name, value):
        if hasattr(self, attribute_name):
            setattr(self, attribute_name, value)
        else:
            raise AttributeError(f"Attribute '{attribute_name}' not found in Products class.")

    def get_attributes(self):
        return self.__dict__


class Subcategory:
    """
        usar este objecto ayuda a generar nodos de busqueda
    """
    def __init__(self, name, element: WebElement, urlSubcategory, categoryName, extrafields, products: list = []):
        self.name = name
        self.url = urlSubcategory
        self.element = element
        self.category = categoryName
        self.extras = extrafields
        self.products = products

    def goToSubCatory(self, webDriver: WebDriver):
        result = wait(webDriver, 2).until(EC.element_to_be_clickable(self.element))
        result.click()
    
    def set_product(self, product: Products):
        self.products.append(product)