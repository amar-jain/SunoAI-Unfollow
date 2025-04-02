
`suno-unfollow.py` is a Python script that uses Playwright to automate the process of identifying and unfollowing users who don't follow you back on the Suno platform.

## Prerequisites

- Python 3.8 or higher
- Microsoft Edge browser installed (used as the browser engine)
- Basic command-line knowledge

## Setup Instructions

### 1. Install Python
- Download and install Python from [python.org](https://www.python.org/downloads/)
- Verify installation:
  `python --version`
  or
  `python3 --version`

### 2. Create Project Directory
```bash
mkdir sunobot
cd sunobot
```

### 3. Set Up Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate
```

### 4. Install Dependencies
```bash
pip install playwright
pip install asyncio
```

### 5. Install Playwright Browsers
```bash
playwright install msedge
```

### 6. Save the Script
- Copy the provided Python code (`suno-unfollow.py`) into your project directory

## Usage Instructions

### Running the Bot
1. Activate the virtual environment (if not already active):
   ```bash
   # Windows
   venv\Scripts\activate
   ```

2. Run the script:
   ```bash
   python suno-unfollow.py
   ```

3. The browser will launch and navigate to suno.com:
   - If not logged in, you'll need to manually log in to your Suno account
   - The bot will wait up to 5 minutes for login completion

### Features
- Automatically detects users you follow who don't follow back
- Safely unfollows non-reciprocal followers with rate limiting
- Maintains progress tracking in `unfollow_progress.txt`
- Logs all actions to `logs/suno_unfollow.log`

## Important Notes
- The script uses Microsoft Edge as its browser engine
- Browser data is stored in `.browser_data` directory by default
- Logs are stored in the `logs` directory with rotation (max 10MB per file, 5 backups)
- The script includes rate limiting handling with automatic retries

## Troubleshooting
1. **Login Issues**
   - Ensure you're logged into Suno in the browser that appears
   - Check internet connection
   - Verify Microsoft Edge is installed

2. **Permission Errors**
   - Run command prompt/terminal as administrator
   - Check directory permissions

3. **Dependency Errors**
   - Ensure all packages are installed
   - Update pip: `pip install --upgrade pip`

## Safety Features
- Rate limiting detection and handling
- Multiple retry attempts for failed operations
- Session verification
- Clean shutdown on interruption (Ctrl+C)
- Progress tracking to prevent duplicate actions

## Logs
- Check `logs/suno_unfollow.log` for detailed execution information
- Logs include timestamps, success/failure messages, and error details

## Disclaimer
Use this tool at your own risk. Ensure compliance with Suno's terms of service. The author is not responsible for any account restrictions that may result from using this automation tool.
