"""Run outside the checkout with the built wheel installed."""

from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import clearagent
from clearagent import create_agent
from clearagent.runtime.providers.base import FakeProvider, ProviderResponse


assert version("clearagent") == clearagent.__version__
assert Path(clearagent.__file__).with_name("py.typed").is_file()
with TemporaryDirectory() as directory:
    path = Path(directory) / "trace.sqlite"
    agent = create_agent(
        name="wheel-consumer", model="openai:test", trace_db_path=path,
        provider=FakeProvider([ProviderResponse.fake_text("installed engine works")]),
    )
    assert agent.run("hello").output == "installed engine works"
    assert path.is_file()
subprocess.run([sys.executable, "-m", "clearagent", "--help"], check=True, capture_output=True)
print(f"Public wheel verified: {clearagent.__file__}")
