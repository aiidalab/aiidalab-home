from selenium.webdriver.common.by import By


def test_single_app(selenium_driver, final_screenshot):
    url_params = {"app": "aiidalab-widgets-base"}
    selenium = selenium_driver("single_app.ipynb", url_params)
    selenium.set_window_size(1440, 828)
    selenium.set_window_size(950, 1000)
    selenium.find_element(By.XPATH, "//button[contains(.,'Uninstall')]")
    selenium.find_element(By.XPATH, "//button[contains(.,'Install')]")
