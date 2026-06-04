"""Job handlers — import submodules so @register decorators run."""

from . import analyze as _analyze  # noqa: F401
from . import ingest as _ingest  # noqa: F401
from . import transcribe as _transcribe  # noqa: F401
