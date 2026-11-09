"""
Async HTTP client for fetching property pages
"""

import asyncio
import logging
from typing import List, Optional
import httpx
from .models import PropertyInfo
from .data_parser import parse_property_html
from .config import HEADERS, TIMEOUT, RETRIES, RATE_LIMIT_DELAY, CONCURRENCY, REQUEST_DELAY


class PropertyFetcher:
    """Async HTTP client for fetching property data"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    async def fetch_html(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        """Fetch HTML content for a single URL with retries"""
        for attempt in range(1, RETRIES + 1):
            try:
                response = await client.get(url, timeout=TIMEOUT)
                
                if response.status_code == 200 and response.text:
                    return response.text
                
                # Handle rate limiting
                elif response.status_code == 429:
                    wait_time = RATE_LIMIT_DELAY * (2 ** attempt)
                    self.logger.warning(f"Rate limited for {url}, waiting {wait_time}s")
                    await asyncio.sleep(wait_time)
                    continue
                
                # Handle client errors (don't retry)
                elif 400 <= response.status_code < 500:
                    self.logger.warning(f"Client error {response.status_code} for {url}")
                    break
                
                # Handle server errors (retry)
                elif response.status_code >= 500:
                    self.logger.warning(f"Server error {response.status_code} for {url}, attempt {attempt}")
                    await asyncio.sleep(RATE_LIMIT_DELAY * attempt)
                    continue
                    
            except httpx.TimeoutException:
                self.logger.warning(f"Timeout for {url}, attempt {attempt}")
                await asyncio.sleep(RATE_LIMIT_DELAY * attempt)
                
            except httpx.RequestError as e:
                self.logger.warning(f"Request error for {url}: {e}, attempt {attempt}")
                await asyncio.sleep(RATE_LIMIT_DELAY * attempt)
                
            except Exception as e:
                self.logger.error(f"Unexpected error for {url}: {e}")
                break
        
        return None
    
    async def process_single_url(self, client: httpx.AsyncClient, url: str) -> PropertyInfo:
        """Process a single URL and return PropertyInfo"""
        html = await self.fetch_html(client, url)
        
        if html:
            return parse_property_html(html, url)
        else:
            return PropertyInfo(url=url, status="fetch_failed")
    
    async def fetch_all_properties(self, urls: List[str]) -> List[PropertyInfo]:
        """Fetch all properties with controlled concurrency"""
        if not urls:
            return []
        
        self.logger.info(f"Starting to fetch {len(urls)} properties with concurrency {CONCURRENCY}")
        
        # Configure HTTP client
        limits = httpx.Limits(
            max_connections=CONCURRENCY,
            max_keepalive_connections=CONCURRENCY // 2
        )
        
        async with httpx.AsyncClient(
            http2=True,
            limits=limits,
            headers=HEADERS,
            timeout=TIMEOUT,
            follow_redirects=True
        ) as client:
            
            # Use semaphore to control concurrency
            semaphore = asyncio.Semaphore(CONCURRENCY)
            
            async def process_with_semaphore(url: str) -> PropertyInfo:
                async with semaphore:
                    result = await self.process_single_url(client, url)
                    await asyncio.sleep(REQUEST_DELAY)  # be polite: pause between requests
                    return result
            
            # Process all URLs
            tasks = [process_with_semaphore(url) for url in urls]
            
            # Execute with progress tracking
            results = []
            completed = 0
            
            for coro in asyncio.as_completed(tasks):
                result = await coro
                results.append(result)
                completed += 1
                
                if completed % 50 == 0 or completed == len(urls):
                    self.logger.info(f"Completed {completed}/{len(urls)} properties")
            
        # Log statistics
        success_count = sum(1 for r in results if r.status == "success")
        failed_count = len(results) - success_count
        
        self.logger.info(f"Fetching complete. Success: {success_count}, Failed: {failed_count}")
        
        return results


async def fetch_properties(urls: List[str]) -> List[PropertyInfo]:
    """Convenience function to fetch properties"""
    fetcher = PropertyFetcher()
    return await fetcher.fetch_all_properties(urls)