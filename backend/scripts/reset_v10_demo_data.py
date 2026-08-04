"""Historical compatibility wrapper for the current demo reset.

New documentation and automation should call ``reset_v13_demo_data.py``.
This filename remains executable so older local workflows do not break.
"""

from reset_v7_demo_data import main


if __name__ == "__main__":
    raise SystemExit(main())
