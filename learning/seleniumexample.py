from webdriver_manager.chrome  import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.webdriver import Chrome, ChromeOptions
import time

from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait as wait 
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

USERNAME = 'standard_user'
PASSWORD = 'secret_sauce'

def scrapping():
    service = Service(ChromeDriverManager().install())
    options = ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    driver = Chrome(service=service, options=options)
    driver.get("https://www.saucedemo.com/")

    user_input = driver.find_element(By.ID, "user-name")
    time.sleep(2)
    user_input.send_keys(USERNAME)

    password_input = driver.find_element(By.ID, "password")
    time.sleep(2)
    password_input.send_keys(PASSWORD)

    button = driver.find_element(By.ID, "login-button")
    time.sleep(2)
    button.click()

    soup = BeautifulSoup(driver.page_source, "html.parser")
    elementsCart = soup.find_all("div", "inventory_item")

    for element in elementsCart:
        price = element.find("div", attrs={"data-test": "inventory-item-price"})
        button = element.find("button", attrs={"class": "btn btn_primary btn_small btn_inventory"})
        priceConvert = float(price.get_text().replace("$", ""))
        button_name = button.get("name")
        if (priceConvert <= 10):
            button_add_cart = driver.find_element(By.NAME, button_name)
            button_add_cart.click()
        time.sleep(1.5)
    
    button_cart = driver.find_element(By.XPATH, "//*[@data-test='shopping-cart-link']")
    button_cart.click()

    button_checkout = driver.find_element(By.ID, "checkout")
    button_checkout.click()
    
    first_name = driver.find_element(By.ID, 'first-name').send_keys("python")
    lastname = driver.find_element(By.ID, 'last-name').send_keys("scrrapp")
    postal_code = driver.find_element(By.ID, 'postal-code').send_keys("661001")
    time.sleep(2)
    driver.find_element(By.XPATH, "//*[@data-test='continue']").click()

    driver.find_element(By.NAME, "finish").click()
    time.sleep(2)
    driver.find_element(By.ID, "back-to-products").click()

    time.sleep(5)
    driver.quit()

scrapping()