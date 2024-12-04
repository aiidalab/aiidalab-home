from selenium.webdriver.common.by import By


def test_home_notification(selenium_driver, create_warning_file, final_screenshot):
    selenium = selenium_driver("start.ipynb")
    selenium.set_window_size(1000, 941)
    notifications = selenium.find_elements(By.CLASS_NAME, "home-notification")
    assert len(notifications) == 1
    home_warning = notifications[0]
    content_element = home_warning.find_element(By.TAG_NAME, "p")
    assert content_element.text == "Warning!"
