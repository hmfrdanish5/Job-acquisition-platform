"""
Tuneable settings for the Job Acquisition Platform.
"""

import os

SEARCH = {
    "default_max_pages": 3,
    "max_pages_limit": 5,
}

DATABASE = {
    "path": os.path.join("data", "jobs.db"),
    "auto_init": True,
    "enabled": True,
}

IMPORT = {
    "default_source": "careers",
}

DASHBOARD = {
    "host": "127.0.0.1",
    "port": 5000,
    "debug": False,
    "page_size": 25,
}

TIMING = {
    "page_load_timeout_ms": 25_000,
    "network_idle_timeout_s": 8.0,
    "between_page_delay_s": 1.0,
}

BROWSER = {
    "headless": True,
    "context": {
        "viewport": {"width": 1366, "height": 768},
        "locale": "en-US",
        "java_script_enabled": True,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    },
}

CHALLENGE = {
    "body_phrases": [
        "verify you are human",
        "just a moment",
        "checking your browser",
        "attention required",
        "enable javascript and cookies",
        "cf-browser-verification",
        "cloudflare",
        "access denied",
        "403 forbidden",
        "unusual traffic",
        "are you a robot",
        "captcha",
    ],
    "url_fragments": ["/cdn-cgi/", "/challenge", "/sorry", "/errors"],
    "body_snippet_length": 1200,
}

EXPORT = {
    "output_file": "jobs.csv",
    "csv_columns": [
        "company_name",
        "job_title",
        "job_location",
        "posting_date",
        "salary",
        "job_url",
    ],
    "encoding": "utf-8-sig",
    "preview_rows": 5,
    "missing_value": "N/A",
}

DEBUG = {
    "enabled": True,
    "output_dir": "debug",
    "save_html_snapshot": True,
    "save_screenshot": True,
    "max_snapshots_per_run": 8,
}

LOGGING = {
    "level": "INFO",
    "format": "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
    "datefmt": "%H:%M:%S",
}
