from selenium.webdriver.common.by import By


def test_process_list_notebook(selenium_driver, final_screenshot):
    selenium = selenium_driver("process_list.ipynb")
    selenium.find_element(By.XPATH, '//button[text()="Update now"]')


def test_process_notebook(selenium_driver, final_screenshot):
    selenium = selenium_driver("process.ipynb")
    selenium.find_element(By.XPATH, '//label[text()="Select input:"]')
