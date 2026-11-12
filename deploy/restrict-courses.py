"""Remove the legacy root entry points, keeping /courses and its API intact."""
from pathlib import Path
import sys

config, courses = map(Path, sys.argv[1:])
host = config.read_text()
routes = courses.read_text()
old_fallback = "  location / {\n    try_files $uri /index.html =404;\n  }"
new_fallback = "  location / {\n    return 404;\n  }"
old_root = "location = / {\n    return 302 /courses/;\n}"
new_root = "location = / {\n    return 404;\n}"
if old_fallback not in host and new_fallback not in host:
    raise SystemExit("Unexpected root fallback; inspect Nginx before changing it.")
if old_root not in routes and new_root not in routes:
    raise SystemExit("Unexpected courses root redirect; inspect Nginx before changing it.")
config.write_text(host.replace(old_fallback, new_fallback))
courses.write_text(routes.replace(old_root, new_root))
