#!/usr/bin/env python3
"""The pinned design set as the review service reads it from the runtime: review-serve.py's _design_set, through its one
load of scripts/design-set-sync. Prints JSON {"tokens": block, "marks": {name: svg}, "icons": {name: svg},
"find": {file name: text}}.

usage: design-set.py   (the browser tests read the set the runtime holds through it)
"""

import importlib.util
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("review_serve_design_set", ROOT / "skill/review-spec/assets/review-serve.py")
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)
tokens, marks, icons, find = serve._design_set((ROOT / serve._RUNTIME_PATH).read_text())
json.dump({"tokens": tokens, "marks": marks, "icons": icons, "find": find}, sys.stdout)
