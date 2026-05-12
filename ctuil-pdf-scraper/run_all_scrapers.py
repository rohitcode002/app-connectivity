import asyncio
import aiohttp
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Adjust the base URL if your app runs on a different host/port
BASE_URL = "http://127.0.0.1:8000/api/v1"

# All scraping endpoints except /api/v1/scrape/pfcclindia-tender
ENDPOINTS = [
    "/scrape/transmission-reports",
    "/scrape/potential-re-zones",
    "/scrape/nct-meetings",
    "/scrape/ists-consultation-meeting",
    "/scrape/ists-joint-coordination-meeting",
    "/scrape/regenerators",
    "/scrape/reallocation-meetings",
    "/scrape/bidding-calendar",
    "/scrape/compliance-fc",
    "/scrape/monitoring-connectivity",
    "/scrape/renewable-energy",
    "/scrape/substation-bulk-consumers",
    "/scrape/gna-connectivity-fresh",
]

async def call_endpoint(session: aiohttp.ClientSession, endpoint: str):
    """
    Make a POST request to the given endpoint.
    """
    url = f"{BASE_URL}{endpoint}"
    logging.info(f"Starting POST request to {url}")
    start_time = time.time()
    
    try:
        # Assuming the scrapers do not need any payload. Adjust json={} if needed.
        async with session.post(url) as response:
            status = response.status
            # Read the response (we use text() to avoid assuming it's JSON if it fails)
            text = await response.text()
            elapsed = time.time() - start_time
            logging.info(f"Completed POST to {url} with status {status} in {elapsed:.2f}s")
            return endpoint, status, text
    except Exception as e:
        elapsed = time.time() - start_time
        logging.error(f"Failed POST to {url} in {elapsed:.2f}s: {e}")
        return endpoint, None, str(e)

async def main():
    logging.info(f"Starting concurrent execution of {len(ENDPOINTS)} endpoints...")
    overall_start_time = time.time()
    
    # Disable timeout since scraping tasks can sometimes take a long time
    timeout = aiohttp.ClientTimeout(total=None)
    
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Create a task for each endpoint
        tasks = [call_endpoint(session, endpoint) for endpoint in ENDPOINTS]
        
        # Run all tasks concurrently
        results = await asyncio.gather(*tasks)
        
    logging.info("=========================================")
    logging.info(f"All requests completed in {time.time() - overall_start_time:.2f}s")
    logging.info("Summary of results:")
    for endpoint, status, text in results:
        if status is None:
            logging.info(f"[ERROR] {endpoint} -> Failed with exception")
        else:
            # Optionally log the response length or snippet
            snippet = (text[:50] + '...') if len(text) > 50 else text
            logging.info(f"[STATUS {status}] {endpoint} -> Response: {snippet}")

if __name__ == "__main__":
    # Ensure Windows works correctly with asyncio
    if __import__("sys").platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())
