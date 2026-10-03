# Bounded parent/child cancellation

Read parent.py and child.py, then propose argv execution with the local Python
interpreter: `-B parent.py READY_DIRECTORY`. The trusted harness supplies an
external ready directory and scripted native approval/cancel decisions.
Only the explicit command grant is available. No source edits, MCP server,
external network or shell is permitted. The finite scripts wait 45 seconds;
the harness must exercise both earlier cancellation and run deadline expiry
and bind OS handles to both live processes before checking their exit.
