#!/usr/bin/env python
from selenium.webdriver.common.by import By
from time import sleep


def test_uninstall_install_widgets_base(selenium, url):
    selenium.get(url("apps/apps/home/single_app.ipynb?app=aiidalab-widgets-base"))
    selenium.set_window_size(1440, 828)
    selenium.find_element(By.XPATH, "//button[contains(.,\'Uninstall\')]").click()
    selenium.get_screenshot_as_file(f'screenshots/manage-app-aiidalab-widgets-base-uninstalled.png')
    selenium.find_element(By.XPATH, "//button[contains(.,\'Install\')]").click()
    selenium.get_screenshot_as_file(f'screenshots/manage-app-aiidalab-widgets-base-installed.png')
