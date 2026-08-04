"""Historical schema-v8 compatibility wrapper for the current demo reset.

New documentation and automation should call ``reset_v12_demo_data.py``.
"""

from reset_v7_demo_data import main


if __name__ == "__main__":
    raise SystemExit(main())
