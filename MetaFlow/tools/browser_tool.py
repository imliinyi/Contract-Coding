import base64
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def browse_and_capture(url: str = "http://localhost:5000") -> dict:
    """
    Navigates to a URL in a headless browser, takes a full-page screenshot,
    and returns it as a base64 encoded string.

    Args:
        url: The URL to visit.

    Returns:
        A dictionary containing the base64 encoded screenshot and the page source.
    """
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080") 

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

        driver.get(url)
        driver.implicitly_wait(5)
        
        screenshot_base64 = driver.get_full_page_screenshot_as_base64()
        page_source = driver.page_source

        driver.quit()

        return {
            "screenshot": screenshot_base64,
            "page_source": page_source
        }
    except Exception as e:
        return {"error": f"An error occurred while browsing: {str(e)}"}

browse_and_capture_schema = {
    "type": "function",
    "function": {
        "name": "browse_and_capture",
        "description": "Navigates to a URL, takes a full-page screenshot, and returns the screenshot and page source.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to visit and capture."
                }
            },
            "required": ["url"]
        }
    }
}


def capture_with_console(url: str, timeout: int = 6) -> dict:
    """
    Navigates to a URL in a headless browser, collects browser console logs,
    takes a full-page screenshot, and returns screenshot, page source, and console logs.

    Args:
        url: The URL to visit.
        timeout: Page load timeout in seconds.

    Returns:
        A dictionary containing the base64 screenshot, page source, and console logs list.
    """
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        try:
            driver.set_page_load_timeout(timeout)
        except Exception:
            pass

        driver.get(url)
        driver.implicitly_wait(5)

        screenshot_base64 = driver.get_full_page_screenshot_as_base64()
        page_source = driver.page_source
        console_logs = []
        try:
            logs = driver.get_log('browser')
            for entry in logs:
                console_logs.append({
                    'level': entry.get('level'),
                    'message': entry.get('message')
                })
        except Exception:
            console_logs = []

        driver.quit()

        return {
            "screenshot": screenshot_base64,
            "page_source": page_source,
            "console_logs": console_logs
        }
    except Exception as e:
        return {"error": f"An error occurred while browsing (console): {str(e)}"}

capture_with_console_schema = {
    "type": "function",
    "function": {
        "name": "capture_with_console",
        "description": "Headless visit to a URL, returns screenshot, page source, and browser console logs.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to visit."},
                "timeout": {"type": "integer", "description": "Page load timeout in seconds.", "default": 6}
            },
            "required": ["url"]
        }
    }
}
