from time import sleep

# from selenium.webdriver.common.by import By


def test_single_app(selenium_driver, final_screenshot):
    selenium = selenium_driver("single_app.ipynb?app=aiidalab-widgets-base")
    selenium.set_window_size(1440, 828)
    # selenium.find_element(By.XPATH, "//button[contains(.,'Uninstall')]")
    # selenium.find_element(By.XPATH, "//button[contains(.,'Install')]")
    sleep(10)
