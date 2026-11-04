import shutil
import subprocess
from pathlib import Path


def test_cchh_feedback_close_preserves_timers_dynamic_swaps_and_other_verticals():
    node=shutil.which('node')
    assert node
    subprocess.run([node,str(Path(__file__).with_name('human_capital_notices.node.js'))],check=True)
