"""
URL collector using Selenium for dynamic content loading
"""

import os
import time
import logging
from typing import List, Optional, Set
from urllib.parse import urlsplit, urlunsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import (
    TimeoutException, 
    StaleElementReferenceException, 
    ElementClickInterceptedException,
    WebDriverException
)

from .config import WAIT_TIME, HEADLESS, PAGE_LOAD_TIMEOUT, SELECTORS, SELENIUM_CACHE_DIR


def clean_listing_url(href: str) -> str:
    """Drop query string and fragment (robots.txt disallows ?search_id=, ?portfolio_ref=, ...)."""
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class URLCollector:
    """Collects property URLs of one neighbourhood using Selenium"""

    def __init__(self, listing_url: str, max_urls: Optional[int] = None):
        self.listing_url = listing_url
        self.max_urls = max_urls
        self.driver = None
        self.wait = None
        self.logger = logging.getLogger(__name__)

    def _setup_driver(self) -> webdriver.Chrome:
        """Setup Chrome driver (uses the installed Chrome; driver cached inside .venv)"""
        # Selenium Manager downloads ChromeDriver into this folder instead of ~/.cache
        SELENIUM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("SE_CACHE_PATH", str(SELENIUM_CACHE_DIR))

        options = Options()
        if HEADLESS:
            options.add_argument("--headless=new")

        # Performance / stability options
        options.add_argument("--incognito")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")

        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        return driver
    
    def _wait_for_initial_load(self) -> bool:
        """Wait for initial page load and first cards"""
        try:
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["listing_cards"]))
            )
            time.sleep(2)  # Buffer for lazy loading
            return True
        except TimeoutException:
            self.logger.error("Initial page load failed - no listing cards found")
            return False
    
    def _collect_current_urls(self) -> Set[str]:
        """Collect all URLs currently visible on page"""
        urls = set()
        try:
            cards = self.driver.find_elements(By.CSS_SELECTOR, SELECTORS["listing_cards"])
            for card in cards:
                try:
                    href = card.get_attribute("href")
                    if href and href.startswith("https"):
                        urls.add(clean_listing_url(href))
                except StaleElementReferenceException:
                    continue  # Element changed, skip it
        except Exception as e:
            self.logger.warning(f"Error collecting URLs: {e}")
        
        return urls
    
    def _find_load_more_button(self):
        """Find the load more button"""
        try:
            return self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["load_more_btn"]))
            )
        except TimeoutException:
            return None
    
    def _click_load_more(self, button) -> bool:
        """Attempt to click the load more button"""
        try:
            # Check if button is disabled
            if (button.get_attribute("disabled") or 
                button.get_attribute("aria-disabled") == "true"):
                return False
            
            # Scroll to button
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});", button
            )
            time.sleep(1)
            
            # Try regular click first
            try:
                button.click()
            except (ElementClickInterceptedException, StaleElementReferenceException):
                # Fallback to JavaScript click
                self.driver.execute_script("arguments[0].click();", button)
            
            return True
            
        except Exception as e:
            self.logger.warning(f"Failed to click load more button: {e}")
            return False
    
    def _wait_for_new_content(self, previous_count: int) -> bool:
        """Wait for new content to load after clicking load more"""
        try:
            self.wait.until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, SELECTORS["listing_cards"])) > previous_count
            )
            time.sleep(1)  # Small buffer for complete loading
            return True
        except TimeoutException:
            return False
    
    def collect_all_urls(self) -> List[str]:
        """Main method to collect all property URLs"""
        self.logger.info(f"Starting URL collection from: {self.listing_url}")

        self.driver = self._setup_driver()
        self.wait = WebDriverWait(self.driver, WAIT_TIME)

        try:
            # Load initial page
            self.driver.get(self.listing_url)
            
            if not self._wait_for_initial_load():
                return []
            
            all_urls = set()
            no_progress_count = 0
            max_no_progress = 3
            
            while len(all_urls)<=1e6:
                # Collect current URLs
                current_urls = self._collect_current_urls()
                new_urls = current_urls - all_urls
                
                if new_urls:
                    all_urls.update(new_urls)
                    self.logger.info(f"Found {len(new_urls)} new URLs. Total: {len(all_urls)}")
                    no_progress_count = 0
                else:
                    no_progress_count += 1
                    self.logger.info(f"No new URLs found. No progress count: {no_progress_count}")

                # Stop early once we have enough URLs
                if self.max_urls and len(all_urls) >= self.max_urls:
                    self.logger.info(f"Reached the limit of {self.max_urls} URLs")
                    break

                # Check for load more button
                button = self._find_load_more_button()
                if not button:
                    self.logger.info("No load more button found - reached end")
                    break
                
                # Try to click load more
                previous_count = len(self.driver.find_elements(By.CSS_SELECTOR, SELECTORS["listing_cards"]))
                
                if not self._click_load_more(button):
                    self.logger.info("Load more button is disabled - reached end")
                    break
                
                # Wait for new content
                if not self._wait_for_new_content(previous_count):
                    no_progress_count += 1
                    self.logger.warning(f"No new content loaded after click. Attempt {no_progress_count}")
                    
                    if no_progress_count >= max_no_progress:
                        self.logger.info("Multiple failed attempts - assuming we've reached the end")
                        break
            
            final_urls = sorted(all_urls)[: self.max_urls] if self.max_urls else list(all_urls)
            self.logger.info(f"Collection complete. Total URLs collected: {len(final_urls)}")
            return final_urls
            
        except WebDriverException as e:
            self.logger.error(f"WebDriver error: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Unexpected error during URL collection: {e}")
            return []
        finally:
            if self.driver:
                self.driver.quit()


def collect_urls(listing_url: str, max_urls: Optional[int] = None) -> List[str]:
    """Convenience function to collect the URLs of one neighbourhood"""
    collector = URLCollector(listing_url, max_urls)
    return collector.collect_all_urls()