from urllib.parse import urlsplit

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as ec
from selenium.webdriver.support.wait import WebDriverWait


def check_classic_shortcut(selenium, title, fragment):
    home_path = urlsplit(selenium.current_url).path
    prefix = home_path.split("/apps/", 1)[0]
    handles = selenium.window_handles
    selenium.find_element(By.CSS_SELECTOR, f'a[title="{title}"]').click()
    WebDriverWait(selenium, 30).until(ec.new_window_is_opened(handles))
    selenium.switch_to.window(selenium.window_handles[-1])
    WebDriverWait(selenium, 30).until(
        ec.presence_of_element_located((By.ID, "notebook_list"))
    )

    url = urlsplit(selenium.current_url)
    assert url.path == f"{prefix}/nbclassic/tree/"
    assert url.fragment == fragment
    if fragment:
        WebDriverWait(selenium, 30).until(
            ec.visibility_of_element_located((By.ID, "running"))
        )


def test_file_manager(selenium_driver, final_screenshot):
    check_classic_shortcut(selenium_driver("start.ipynb"), "File Manager", "")


def test_task_manager(selenium_driver, final_screenshot):
    check_classic_shortcut(selenium_driver("start.ipynb"), "Task Manager", "running")
