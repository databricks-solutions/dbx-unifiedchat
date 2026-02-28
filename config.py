import warnings
from src.multi_agent.core.config import *

warnings.warn(
    "The root config.py is deprecated and will be removed in a future version. "
    "Please import from src.multi_agent.core.config instead.",
    DeprecationWarning,
    stacklevel=2
)
