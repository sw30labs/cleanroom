"""Cleanroom — Web Entry

Module entry point for the dashboard: python -m cleanroom.web.

Author: Nic Cravino
Email: spidernic@me.com
LinkedIn: https://www.linkedin.com/in/nic-cravino
Date: August 13, 2026
License: MIT
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())