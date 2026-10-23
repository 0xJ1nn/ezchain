"""ezCHAIN strategy plugin SDK (Apache-2.0).

A plugin imports only this package. It declares itself with ``ezchain.plugin.json`` and
returns intents; the host owns market data, risk, sizing, quoting, execution and the
ledger.
"""

from .errors import (
    ManifestError,
    ParamsError,
    PluginContractError,
    PluginError,
    PluginLoadError,
)
from .manifest import (
    MANIFEST_FILENAME,
    SDK_VERSION,
    PluginManifest,
    check_compatible,
    load_manifest,
    parse_manifest,
)
from .params import validate_params
from .strategy import Strategy
from .types import (
    Account,
    Allocation,
    Candle,
    Fill,
    Intent,
    Mode,
    PluginContext,
    Position,
    Side,
    Signal,
    Snapshot,
)
from .types import PluginContext as Context

__all__ = [
    "MANIFEST_FILENAME",
    "SDK_VERSION",
    "Account",
    "Allocation",
    "Candle",
    "Context",
    "Fill",
    "Intent",
    "ManifestError",
    "Mode",
    "ParamsError",
    "PluginContext",
    "PluginContractError",
    "PluginError",
    "PluginLoadError",
    "PluginManifest",
    "Position",
    "Side",
    "Signal",
    "Snapshot",
    "Strategy",
    "check_compatible",
    "load_manifest",
    "parse_manifest",
    "validate_params",
]
