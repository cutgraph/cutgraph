import sys

if sys.flags.optimize:
    raise SystemExit("Studio requires Python without optimization; remove -O or PYTHONOPTIMIZE.")
