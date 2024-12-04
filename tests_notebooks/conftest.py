import os
from pathlib import Path
from time import sleep
from urllib.parse import urljoin

import pytest
import requests
import selenium.webdriver.support.expected_conditions as ec
from requests.exceptions import ConnectionError
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait


def is_responsive(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            return True
    except ConnectionError:
        return False


@pytest.fixture(scope="session")
def screenshot_dir():
    sdir = Path.joinpath(Path.cwd(), "screenshots")
    try:
        os.mkdir(sdir)
    except FileExistsError:
        pass
    return sdir


@pytest.fixture(scope="session")
def docker_compose_file(pytestconfig):
    return str(Path(pytestconfig.rootdir) / "tests_notebooks" / "docker-compose.yml")


@pytest.fixture(scope="session")
def docker_compose(docker_services):
    return docker_services._docker_compose


@pytest.fixture(scope="session")
def aiidalab_exec(docker_compose, nb_user):
    def execute(command, user=None, **kwargs):
        workdir = "/home/{nb_user}/apps/home"
        if user is None:
            user = nb_user
        command = f"exec --workdir {workdir} -T --user={user} aiidalab {command}"
        return docker_compose.execute(command, **kwargs)

    return execute


@pytest.fixture(scope="session")
def nb_user():
    return "jovyan"


@pytest.fixture
def create_warning_file(nb_user, aiidalab_exec):
    config_folder = f"/home/{nb_user}/.aiidalab"
    aiidalab_exec(
        f"mkdir -p {config_folder} && chmod a+xr {config_folder}", user="root"
    )
    aiidalab_exec("echo 'Warning!' > {config_folder}/home_app_warning.md", user=nb_user)


@pytest.fixture(scope="session", autouse=True)
def notebook_service(docker_ip, docker_services, aiidalab_exec, nb_user):
    """Ensure that HTTP service is up and responsive."""
    # Directory ~/apps/home/ is mounted by docker,
    # make it writeable for jovyan user, needed for `pip install`
    aiidalab_exec(f"chmod -R a+rw /home/{nb_user}/apps/home", user="root")

    aiidalab_exec("pip install --no-cache-dir .")

    # `port_for` takes a container port and returns the corresponding host port
    port = docker_services.port_for("aiidalab", 8888)
    url = f"http://{docker_ip}:{port}"
    token = os.environ["JUPYTER_TOKEN"]
    docker_services.wait_until_responsive(
        timeout=30.0, pause=0.1, check=lambda: is_responsive(url)
    )
    return url, token


@pytest.fixture(scope="function")
def selenium_driver(selenium, notebook_service):
    def _selenium_driver(nb_path, url_params=None):
        url, token = notebook_service
        url_with_token = urljoin(url, f"apps/apps/home/{nb_path}?token={token}")
        if url_params is not None:
            for key, value in url_params.items():
                url_with_token += f"&{key}={value}"
        selenium.get(f"{url_with_token}")
        # By default, let's allow selenium functions to retry for 60s
        # till a given element is loaded, see:
        # https://selenium-python.readthedocs.io/waits.html#implicit-waits
        selenium.implicitly_wait(90)
        window_width = 800
        window_height = 600
        selenium.set_window_size(window_width, window_height)

        selenium.find_element(By.ID, "ipython-main-app")
        selenium.find_element(By.ID, "notebook-container")
        selenium.find_element(By.ID, "appmode-busy")
        # We wait until the appmode spinner disappears. However,
        # this does not seem to be fully robust, as the spinner might flash
        # while the page is still loading. So we add explicit sleep here as well.
        WebDriverWait(selenium, 240).until(
            ec.invisibility_of_element((By.ID, "appmode-busy"))
        )
        sleep(5)

        return selenium

    return _selenium_driver


@pytest.fixture
def final_screenshot(request, screenshot_dir, selenium):
    """Take screenshot at the end of the test.
    Screenshot name is generated from the test function name
    by stripping the 'test_' prefix
    """
    screenshot_name = f"{request.function.__name__[5:]}.png"
    screenshot_path = Path.joinpath(screenshot_dir, screenshot_name)
    yield
    selenium.get_screenshot_as_file(screenshot_path)


@pytest.fixture
def firefox_options(firefox_options):
    firefox_options.add_argument("--headless")
    return firefox_options


@pytest.fixture
def chrome_options(chrome_options):
    chrome_options.add_argument("--headless")
    return chrome_options
