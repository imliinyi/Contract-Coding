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

        # Automatically download and manage the chromedriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

        driver.get(url)
        # Wait for the page to load completely
        driver.implicitly_wait(5)
        
        # Take a full-page screenshot
        screenshot_base64 = driver.get_full_page_screenshot_as_base64()
        page_source = driver.page_source

        driver.quit()

        return {
            "screenshot": screenshot_base64,
            "page_source": page_source
        }
    except Exception as e:
        return {"error": f"An error occurred while browsing: {str(e)}"}

# OpenAI Tool Schema for the above function
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