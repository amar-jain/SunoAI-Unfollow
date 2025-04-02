import asyncio
import logging
import random
import os
import sys
import time
import json 
import re
from typing import List, Set, Optional
from dataclasses import dataclass
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from playwright.async_api import async_playwright, Page, BrowserContext, Response, Browser

@dataclass
class User:
    username: str
    
    @staticmethod
    def validate_username(username: str) -> bool:
        """Validate username format and content"""
        if not username or not isinstance(username, str):
            return False
        cleaned = username.replace('@', '').strip()
        return bool(re.match(r'^[\w\-]{2,30}$', cleaned))

class RateLimitError(Exception):
    """Custom exception for rate limiting"""
    pass

class SessionError(Exception):
    """Custom exception for session issues"""
    pass

class SunoBot:
    def __init__(self, headless: bool = False, user_data_dir: str = None):
        self.headless = headless
        self.base_url = "https://suno.com"
        self.browser = None
        self.playwright = None
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self._setup_logging()
        self.user_data_dir = self._validate_user_data_dir(user_data_dir)
        self.page = None
        self.context = None
        self.MAX_RETRIES = 3
        self.processed_users = set()

    def _validate_user_data_dir(self, user_data_dir: Optional[str]) -> str:
        if user_data_dir:
            full_path = os.path.join(self.script_dir, user_data_dir)
        else:
            full_path = os.path.join(self.script_dir, '.browser_data')
            
        os.makedirs(full_path, exist_ok=True)
        
        test_file = os.path.join(full_path, 'test_write')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
        except Exception as e:
            raise ValueError(f"Directory not writable: {full_path}. Error: {str(e)}")
            
        self.logger.info(f"Using browser data directory: {full_path}")
        return full_path
        
    def _setup_logging(self) -> None:
        self.logger = logging.getLogger('SunoBot')
        self.logger.setLevel(logging.INFO)
        
        log_dir = os.path.join(self.script_dir, 'logs')
        os.makedirs(log_dir, exist_ok=True)
        
        log_path = os.path.join(log_dir, 'suno_unfollow.log')
        fh = RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=5)
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(fh)
        
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(ch)

    async def initialize_browser(self) -> None:
        try:
            if not self.playwright:
                self.playwright = await async_playwright().start()
                
            if not self.browser or not self.context:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=self.user_data_dir,
                    channel="msedge",
                    headless=self.headless,
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    accept_downloads=True,
                    ignore_https_errors=True,
                    bypass_csp=True  # Add this to bypass Content Security Policy
                )
                await self.context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                """)
                self.browser = self.context.browser
                self.logger.info("Browser initialized successfully with persistent context")
        except Exception as e:
            self.logger.error(f"Failed to initialize browser: {str(e)}")
            raise

    async def refresh_cookies(self, page: Page) -> bool:
        """Refresh and verify cookies are working"""
        try:
            cookies = await self.context.cookies()
            if not cookies:
                self.logger.warning("No cookies found, attempting to refresh session")
                await self.verify_session(page)
                return True
                
            # Verify essential cookies are present
            essential_cookies = ['session_id', 'auth_token']  # adjust these names based on actual cookie names
            missing_cookies = [cookie for cookie in essential_cookies 
                             if not any(c['name'] == cookie for c in cookies)]
            
            if missing_cookies:
                self.logger.warning(f"Missing essential cookies: {missing_cookies}")
                await self.verify_session(page)
                return True
                
            return True
        except Exception as e:
            self.logger.error(f"Error refreshing cookies: {str(e)}")
            return False

    @asynccontextmanager
    async def browser_context(self):
        page = None
        try:
            if not self.context:
                await self.initialize_browser()
            
            page = await self.context.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,svg}", lambda route: route.abort())
            await self.verify_session(page)
            
            yield page
            
        except Exception as e:
            self.logger.error(f"Browser context error: {str(e)}")
            raise
        finally:
            try:
                if page:
                    await page.close()
            except Exception as e:
                self.logger.error(f"Error closing page: {str(e)}")

    async def verify_session(self, page: Page) -> bool:
        try:
            # Increased timeout and changed wait_until condition
            await page.goto(f"{self.base_url}/me", timeout=60000, wait_until='load')
            await asyncio.sleep(2)  # Give extra time for the page to settle
            
            login_selectors = [
                '.profile-section',
                '[data-testid="profile"]',
                'div[role="navigation"]',
                'button:has-text("Following")',
                'button:has-text("My Profile")',
                'a:has-text("My Profile")',
                'div:has-text("Following")',
                '.header-user-menu',
                '.user-profile'
            ]
            
            # Try multiple times to find any of the selectors
            for _ in range(3):  # 3 attempts
                for selector in login_selectors:
                    try:
                        element = await page.wait_for_selector(selector, timeout=10000)
                        if element:
                            self.logger.info(f"Login detected via selector: {selector}")
                            return True
                    except:
                        continue
                await asyncio.sleep(2)  # Wait between attempts
            
            # If we get here, we need manual login
            self.logger.info("User not logged in. Please log in manually...")
            self.logger.info("Waiting for login to complete...")
            
            try:
                await page.wait_for_selector(' ,'.join(login_selectors), timeout=300000)
                self.logger.info("Login successful!")
                return True
            except Exception as e:
                current_url = page.url
                if '/me' in current_url or '/profile' in current_url:
                    self.logger.info("Detected profile URL - assuming logged in")
                    return True
                raise SessionError("Login timeout - please try again")
            
        except Exception as e:
            self.logger.error(f"Session verification failed: {str(e)}")
            if isinstance(e, SessionError):
                raise
            raise SessionError("Failed to verify session status")


    async def unfollow_user(self, page: Page, username: str) -> bool:
        """Optimized unfollow method using working auth capture approach"""
        if username in self.processed_users:
            self.logger.info(f"Skipping already processed user: {username}")
            return True

        auth_headers = None

        async def capture_auth_headers(response):
            """Capture authentication headers from response"""
            nonlocal auth_headers
            if "/api/profiles/" in response.url and response.status == 200:
                headers = await response.all_headers()
                request_headers = await response.request.all_headers()
                auth_headers = {
                    'authorization': request_headers.get('authorization', ''),
                    'session-id': headers.get('session-id', ''),
                    'device-id': request_headers.get('device-id', ''),
                    'affiliate-id': request_headers.get('affiliate-id', 'undefined'),
                    'content-type': 'text/plain;charset=UTF-8',
                    'origin': 'https://suno.com',
                    'referer': 'https://suno.com/'
                }
                self.logger.info("Successfully captured auth headers from API response")

        async def ensure_auth_headers():
            """Get fresh auth headers using the working approach"""
            nonlocal auth_headers
            auth_headers = None
            
            # Setup response listener
            page.on("response", capture_auth_headers)
            
            try:
                # Visit the following page to trigger API calls
                await page.goto(f"{self.base_url}/me/following", timeout=30000, wait_until='networkidle')
                await page.wait_for_selector('button:has-text("Following")', timeout=10000)
                await asyncio.sleep(2)
                
                # Scroll to trigger API calls if needed
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(2)
                
                # Wait for headers with timeout
                start_time = time.time()
                while not auth_headers and time.time() - start_time < 20:
                    await asyncio.sleep(1)
                
                if not auth_headers:
                    raise SessionError("Failed to capture auth headers")
                    
                return auth_headers
                
            finally:
                page.remove_listener("response", capture_auth_headers)

        for attempt in range(self.MAX_RETRIES):
            try:
                self.logger.info(f"Attempting to unfollow {username} (attempt {attempt + 1}/{self.MAX_RETRIES})")
                
                # Get fresh auth headers using working approach
                headers = await ensure_auth_headers()
                
                # Prepare API request payload
                payload = {
                    "unfollow": True,
                    "handle": username
                }
                
                # Make API request
                response = await page.request.post(
                    'https://studio-api.prod.suno.com/api/profiles/follow',
                    headers=headers,
                    data=json.dumps(payload)
                )
                
                # Check response status
                if response.status == 204:
                    self.processed_users.add(username)
                    self.logger.info(f"Successfully unfollowed {username}")
                    await asyncio.sleep(random.uniform(30, 60))
                    return True
                    
                elif response.status == 401:
                    self.logger.warning("Session expired, refreshing...")
                    await self.verify_session(page)
                    continue
                    
                elif response.status == 429:
                    retry_after = int(response.headers.get('Retry-After', '60'))
                    self.logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                    await asyncio.sleep(retry_after)
                    continue
                    
                else:
                    response_text = await response.text()
                    self.logger.error(f"Unexpected response: {response.status} - {response_text}")
                    
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(random.uniform(10, 15))
                        continue
                    return False

            except SessionError as se:
                self.logger.error(f"Session error while unfollowing {username}: {str(se)}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(random.uniform(15, 30))
                    continue
                return False
                
            except Exception as e:
                self.logger.error(f"Error unfollowing {username} (attempt {attempt + 1}): {str(e)}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(random.uniform(15, 30))
                    continue
                return False

        return False




    async def get_users(self, page: Page, page_type: str) -> List[User]:
        """Get users using API pagination with auth refresh"""
        MAX_API_RETRIES = 3
        users: Set[str] = set()
        current_page = 1
        total_pages = None
        auth_headers = None

        async def capture_auth_headers(response):
            """Capture authentication headers from response"""
            if (f"/api/profiles/{page_type}" in response.url and 
                response.status == 200):
                nonlocal auth_headers
                headers = await response.all_headers()
                request_headers = await response.request.all_headers()
                auth_headers = {
                    'authorization': request_headers.get('authorization', ''),
                    'session-id': headers.get('session-id', ''),
                    'device-id': request_headers.get('device-id', ''),
                    'affiliate-id': request_headers.get('affiliate-id', 'undefined')
                }
                self.logger.info("Captured fresh authentication headers")

        async def refresh_auth_headers():
            """Refresh authentication headers by reloading the page"""
            nonlocal auth_headers
            auth_headers = None
            self.logger.info("Refreshing authentication headers...")
            
            for retry in range(3):  # Add retries for auth refresh
                try:
                    # First go to home page
                    await page.goto(self.base_url, timeout=60000, wait_until='domcontentloaded')
                    await asyncio.sleep(3)
                    
                    # Then go to target page
                    await page.goto(
                        f"{self.base_url}/me/{page_type}", 
                        timeout=60000, 
                        wait_until='domcontentloaded'
                    )
                    await asyncio.sleep(3)
                    
                    # Scroll and wait for headers
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await asyncio.sleep(2)
                    
                    # Wait for headers with timeout
                    start_time = time.time()
                    while not auth_headers and time.time() - start_time < 20:  # Reduced timeout to 20 seconds
                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                        await asyncio.sleep(1)
                    
                    if auth_headers:
                        self.logger.info("Successfully refreshed authentication headers")
                        return
                        
                except Exception as e:
                    self.logger.warning(f"Auth refresh attempt {retry + 1} failed: {str(e)}")
                    await asyncio.sleep(5)
                    
            # If we get here, all retries failed
            raise Exception("Failed to refresh authentication headers after 3 attempts")

        try:
            # Setup response listener
            page.on("response", capture_auth_headers)
            
            # Initial navigation to capture auth headers
            await page.goto(f"{self.base_url}/me/{page_type}", timeout=60000, wait_until='domcontentloaded')
            await asyncio.sleep(3)
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(2)
            
            # Wait for initial auth headers
            start_time = time.time()
            while not auth_headers and time.time() - start_time < 20:  # Reduced initial timeout
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(1)
            
            if not auth_headers:
                raise Exception("Failed to capture initial authentication headers")
            
            # Process all pages
            while current_page <= (total_pages or 1):
                self.logger.info(f"Fetching page {current_page} of {total_pages or '?'}")
                
                api_retry_count = 0
                while api_retry_count < MAX_API_RETRIES:
                    try:
                        # Prepare headers for fetch
                        headers_str = ', '.join([f"'{k}': '{v}'" for k, v in auth_headers.items()])
                        
                        # Make authenticated API call
                        response_data = await page.evaluate(f"""
                            async () => {{
                                const response = await fetch(
                                    'https://studio-api.prod.suno.com/api/profiles/{page_type}?page={current_page}',
                                    {{
                                        headers: {{{headers_str}}}
                                    }}
                                );
                                if (!response.ok) {{
                                    throw new Error(`HTTP error! status: ${{response.status}}`);
                                }}
                                return await response.json();
                            }}
                        """)
                        
                        # If we got here, request was successful
                        break
                        
                    except Exception as api_error:
                        api_retry_count += 1
                        error_msg = str(api_error)
                        
                        if "HTTP error! status: 502" in error_msg:
                            self.logger.warning(f"Got 502 error, retrying ({api_retry_count}/{MAX_API_RETRIES})...")
                            await asyncio.sleep(5)
                            continue
                        elif "HTTP error! status: 401" in error_msg:
                            self.logger.info("Auth token expired, refreshing...")
                            await refresh_auth_headers()
                            continue
                        else:
                            self.logger.error(f"API call failed with error: {error_msg}")
                            raise

                if api_retry_count == MAX_API_RETRIES:
                    raise Exception(f"Failed to fetch page {current_page} after {MAX_API_RETRIES} retries")
                
                if not response_data or not isinstance(response_data, dict):
                    raise Exception("Invalid API response format")
                
                # Update total pages on first response
                if total_pages is None and 'num_total_profiles' in response_data:
                    total_profiles = response_data['num_total_profiles']
                    total_pages = -(-total_profiles // 20)  # Ceiling division
                    self.logger.info(f"Total profiles: {total_profiles}, Pages: {total_pages}")
                
                if 'profiles' not in response_data:
                    raise Exception("No profiles key in response")
                
                # Process users from current page
                previous_count = len(users)
                for profile in response_data['profiles']:
                    if 'handle' in profile:
                        cleaned_username = profile['handle'].replace('@', '').strip()
                        if User.validate_username(cleaned_username):
                            users.add(cleaned_username)
                
                new_users = len(users) - previous_count
                self.logger.info(f"Added {new_users} users from page {current_page}")
                
                if new_users == 0 and current_page > 1:
                    self.logger.info("No new users found, ending pagination")
                    break
                
                current_page += 1
                await asyncio.sleep(1)
                        
        except Exception as e:
            self.logger.error(f"Error getting {page_type}: {str(e)}")
            raise
        finally:
            page.remove_listener("response", capture_auth_headers)
        
        self.logger.info(f"Final {page_type} count: {len(users)}")
        return [User(username=username) for username in users]


    async def find_and_unfollow_nonreciprocal(self, page: Page) -> None:
        try:
            self.logger.info("Getting list of users you follow...")
            following = await self.get_users(page, "following")
            
            self.logger.info("Getting list of your followers...")
            followers = await self.get_users(page, "followers")
            
            following_usernames = {user.username for user in following}
            follower_usernames = {user.username for user in followers}
            users_to_unfollow = following_usernames - follower_usernames
            
            self.logger.info(f"Found {len(users_to_unfollow)} users who don't follow back")
            
            progress_file = os.path.join(self.script_dir, 'unfollow_progress.txt')
            with open(progress_file, 'w') as f:
                f.write('\n'.join(self.processed_users))
            
            chunk_size = 5
            for i in range(0, len(users_to_unfollow), chunk_size):
                chunk = list(users_to_unfollow)[i:i + chunk_size]
                
                for username in chunk:
                    if await self.unfollow_user(page, username):
                        # Update progress file
                        with open(progress_file, 'a') as f:
                            f.write(f'\n{username}')
                
                if i + chunk_size < len(users_to_unfollow):
                    await asyncio.sleep(random.uniform(60, 120))
                    
        except Exception as e:
            self.logger.error(f"Error in find_and_unfollow_nonreciprocal: {str(e)}")
            raise

    async def handle_rate_limit(self, response: Response) -> None:
        try:
            retry_after = int(response.headers.get('Retry-After', '60'))
        except (ValueError, TypeError):
            retry_after = 60
            
        self.logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
        await asyncio.sleep(retry_after)
        raise RateLimitError(f"Rate limited. Retry after {retry_after} seconds")

    async def cleanup(self) -> None:
        try:
            if hasattr(self, 'page') and self.page:
                try:
                    await self.page.close()
                except Exception:
                    pass
                self.page = None
            
            if hasattr(self, 'context') and self.context:
                try:
                    await self.context.close()
                except Exception:
                    pass
                self.context = None
                
            if hasattr(self, 'browser') and self.browser:
                self.browser = None
                
            if hasattr(self, 'playwright') and self.playwright:
                await self.playwright.stop()
                self.playwright = None
                
            self.logger.info("Cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {str(e)}")

    async def run(self) -> None:
        try:
            await self.initialize_browser()
            
            async with self.browser_context() as page:
                await self.find_and_unfollow_nonreciprocal(page)
                self.logger.info("Bot completed successfully")
                
        except SessionError as e:
            self.logger.error(f"Session error: {str(e)}")
            if "timeout" not in str(e).lower():
                await asyncio.sleep(5)
        except RateLimitError as e:
            self.logger.error(f"Rate limit error: {str(e)}")
        except Exception as e:
            self.logger.error(f"Fatal error in run method: {str(e)}")
        finally:
            await self.cleanup()

def main():
    try:
        # Create and setup event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Create bot instance
        bot = SunoBot(headless=False)
        
        try:
            # Run bot
            loop.run_until_complete(bot.run())
        except KeyboardInterrupt:
            print("\nInterrupted by user. Cleaning up...")
            loop.run_until_complete(bot.cleanup())
        except Exception as e:
            logging.error(f"Bot failed: {str(e)}")
            loop.run_until_complete(bot.cleanup())
        finally:
            # Always clean up event loop
            pending = asyncio.all_tasks(loop)
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            
    except Exception as e:
        logging.error(f"Fatal error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
