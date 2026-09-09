import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "NodeCore"))

from node_core.agents import extract_code_blocks_with_metadata

sample_text = """### ludo_logic.py

```python
import json
class LudoGame:
    pass
```

### ludo_gui.py

```python
import tkinter as tk
class LudoGUI:
    pass
```
"""

blocks = extract_code_blocks_with_metadata(sample_text)
print("Extracted count:", len(blocks))
for b in blocks:
    print(f"File: '{b['filename']}', Lang: '{b['lang']}', Length: {len(b['code'])}")
