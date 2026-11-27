"""PyInstaller runtime hook enabling the dedicated WFSim-linked edition."""

import os

os.environ["RIVEN_WFSIM_EDITION"] = "1"
