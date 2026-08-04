"""Job sources — where postings come from.

`SOURCE` is the active source. It is a module-level binding rather than something
injected through the graph state: there is one source per deployment, it is chosen at
import time, and threading it through every LangGraph channel would add plumbing no
caller varies. Swap the binding here to swap providers.
"""

from jdparser.jobsource.adzuna import AdzunaSource
from jdparser.jobsource.base import JobSource

SOURCE: JobSource = AdzunaSource()

__all__ = ["JobSource", "SOURCE"]
