"""Safe local application used only for documentation screenshots."""
from pathlib import Path

from autody.web_api import create_app

app = create_app(Path(__file__).parent / "fixtures" / "documentation-fixture.yml")
