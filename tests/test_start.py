#!/usr/bin/env python
from selenium.webdriver.common.by import By
from contextlib import contextmanager
import time

@contextmanager
def get_new_windows(selenium, timeout=2):
    handles = set()
    wh_before = selenium.window_handles
    yield handles
    time.sleep(timeout)
    handles.update(set(selenium.window_handles).difference(wh_before))


def test_click_appstore(selenium, url):
    selenium.get(url("apps/apps/home/start.ipynb"))
    with get_new_windows(selenium) as handles:
        selenium.find_element(By.CSS_SELECTOR, ".fa-puzzle-piece").click()
    assert len(handles) == 1
    selenium.switch_to.window(handles.pop())
    time.sleep(10)
    dropdown = selenium.find_element(By.XPATH, "//div[@id=\'notebook-container\']/div[5]/div[2]/div[2]/div/div[3]/div/div[2]/div/select")
    dropdown.find_element(By.XPATH, "//option[. = 'Utilities']").click()
    selenium.find_element(By.CSS_SELECTOR, ".widget-button:nth-child(1)").click()
    selenium.find_element(By.CSS_SELECTOR, ".widget-html-content > h1").click()
    selenium.get_screenshot_as_file(f'screenshots/app-store.png')

def test_click_help(selenium, url):
    selenium.get(url("apps/apps/home/start.ipynb"))
    selenium.set_window_size(1200, 941)
    with get_new_windows(selenium) as handles:
        selenium.find_element(By.CSS_SELECTOR, ".fa-question").click()
    assert len(handles) == 1
    selenium.switch_to.window(handles.pop())
    selenium.find_element(By.CSS_SELECTOR, ".mr-md-2").click()
    selenium.get_screenshot_as_file(f'screenshots/help.png')

def test_click_filemanager(selenium, url):
    selenium.get(url("apps/apps/home/start.ipynb"))
    selenium.set_window_size(1200, 941)
    with get_new_windows(selenium) as handles:
        selenium.find_element(By.CSS_SELECTOR, ".fa-file-text-o").click()
    assert len(handles) == 1
    selenium.switch_to.window(handles.pop())
    selenium.find_element(By.LINK_TEXT, "Running").click()
    selenium.find_element(By.LINK_TEXT, "Clusters").click()
    selenium.find_element(By.LINK_TEXT, "Files").click()
    selenium.get_screenshot_as_file(f'screenshots/file-manager.png')
