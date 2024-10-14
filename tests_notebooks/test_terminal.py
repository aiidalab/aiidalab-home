#!/usr/bin/env python
from time import sleep

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


def test_terminal(selenium_driver, final_screenshot):
    selenium = selenium_driver("start.ipynb")
    selenium.set_window_size(1575, 907)
    selenium.find_element(By.CSS_SELECTOR, ".fa-terminal").click()
    page = selenium.window_handles[-1]
    sleep(3)
    selenium.switch_to.window(page)
    selenium.find_element(By.CSS_SELECTOR, ".xterm-cursor-layer").click()
    selenium.find_element(By.CSS_SELECTOR, ".xterm-helper-textarea").send_keys(
        "jupyter --version"
    )
    selenium.find_element(By.CSS_SELECTOR, ".xterm-helper-textarea").send_keys(
        Keys.ENTER
    )
    sleep(1)
