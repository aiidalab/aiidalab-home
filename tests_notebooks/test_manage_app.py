#!/usr/bin/env python
from selenium.webdriver.common.by import By


def test_single_app(selenium, url, final_screenshot):
    selenium.get(url("apps/apps/home/single_app.ipynb?app=aiidalab-widgets-base"))
    selenium.set_window_size(1440, 828)
    selenium.find_element(By.XPATH, "//button[contains(.,'Uninstall')]")
    selenium.find_element(By.XPATH, "//button[contains(.,'Install')]")
