"""Job handlers — import submodules so @register decorators run."""

from . import analyze as _analyze  # noqa: F401
from . import caption as _caption  # noqa: F401
from . import ingest as _ingest  # noqa: F401
from . import publish as _publish  # noqa: F401
from . import render as _render  # noqa: F401
from . import transcribe as _transcribe  # noqa: F401
