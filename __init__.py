"""Eli Felse Reading - drop-in module entry point.

When installed at data/modules/reading/, the base framework auto-discovers this
file and reads the ACTIVITIES list to register the activity.
"""

try:
    from .src.reading.activity import ReadingActivity
except ImportError:
    # Loaded as a plain top-level module rather than as a package, which is
    # what happens when the repo is used standalone or under pytest.
    from src.reading.activity import ReadingActivity

ACTIVITIES = [ReadingActivity]
