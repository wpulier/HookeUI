"""`make dev` waits for the dev studio by its page title (scripts/studio.sh is_dev_studio): the title the studio
serves must be one it recognises, or make dev gives up after 30 s (it did when the title became "HookeUI Studio")."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_the_dev_studio_check_recognises_the_studio_page_title():
    script = (REPO / "scripts" / "studio.sh").read_text()
    pattern = re.search(r"is_dev_studio\(\) \{[^}]*grep -Eq '([^']+)'", script).group(1)
    title = re.search(r"<title>[^<]*</title>", (REPO / "apps" / "studio" / "index.html").read_text()).group(0)
    assert re.search(pattern, title), f"{title} is not recognised by {pattern}"
