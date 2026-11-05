import os

# The generators' extract date (agents.utils.today): evals and the golden read their datasets as
# of it, so a future-dated HR exit stays future whatever the calendar says. The live app reads
# the real today.
os.environ.setdefault("ANALYSTAI_TODAY", "2026-11-01")
