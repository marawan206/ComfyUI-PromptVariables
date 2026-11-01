import sys
import types
from pathlib import Path

import torch

PACK = Path(__file__).resolve().parents[1]
# ComfyUI root, so the tests also run from inside the pack folder.
sys.path.insert(0, str(PACK.parents[1]))

from comfy.cli_args import args  # noqa: E402

if not torch.cuda.is_available():
    args.cpu = True

# Import the pack as "pack" without running __init__.py, which needs a running PromptServer.
package = types.ModuleType("pack")
package.__path__ = [str(PACK)]
sys.modules["pack"] = package
