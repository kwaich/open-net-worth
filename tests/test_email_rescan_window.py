"""
The _REWIND_UIDS re-scan window mechanism was removed in the simplification of the email
fetch path. The tests that covered that mechanism have been replaced by
test_email_fetch_simple.py, which covers the simplified contract:
  - simple incremental advance (cursor moves to max UID of returned batch)
  - surplus/more=true contract (oldest limit returned, nothing silently skipped)
  - old emails not re-presented (no implicit rewind)
  - per-consumer isolation

This file is intentionally empty.
"""
