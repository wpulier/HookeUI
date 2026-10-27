"""Release-feed boundary checks; real Sparkle installation is verified separately in a VM."""
import plistlib
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

MACOS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MACOS))
import channels  # noqa: E402
SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"


class AppcastTests(unittest.TestCase):
    def test_legacy_automatic_clients_get_interactive_offer_with_valid_payload_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dmg, notes, feed = root / "test.dmg", root / "notes.md", root / "appcast.xml"
            dmg.write_bytes(b"signed payload fixture")
            notes.write_text("# Update\n- Save <drafts> & finish work\n")
            url = "https://example.org/Hooke.dmg?a=1&b=2"
            subprocess.run([sys.executable, str(MACOS / "appcast.py"), "--dmg", str(dmg),
                            "--version", "0.1.1", "--build", "3264", "--url", url,
                            "--signature", "signature-fixture", "--notes", str(notes),
                            "--out", str(feed)], check=True, capture_output=True)
            item = ET.parse(feed).find("./channel/item")
            self.assertEqual(item.findtext(SPARKLE + "minimumAutoupdateVersion"),
                             item.findtext(SPARKLE + "version"))
            enclosure = item.find("enclosure")
            self.assertEqual(enclosure.attrib["url"], url)
            self.assertEqual(int(enclosure.attrib["length"]), dmg.stat().st_size)
            self.assertEqual(enclosure.attrib[SPARKLE + "edSignature"], "signature-fixture")
            self.assertIn("Save &lt;drafts&gt; &amp; finish work", item.findtext("description"))

    def test_packaged_policy_cannot_inherit_old_automatic_install_preference(self):
        template = (MACOS / "HookeUI/Info.plist").read_text()
        for channel in channels.load():  # the template is rendered per channel (channels.py)
            policy = plistlib.loads(channels.info_plist(template, channel, version="0.1.1", build="3264").encode())
            self.assertTrue(policy["SUEnableAutomaticChecks"])
            self.assertFalse(policy["SUAllowsAutomaticUpdates"])
            self.assertFalse(policy["SUAutomaticallyUpdate"])
            self.assertTrue(policy["SURequireSignedFeed"])
            self.assertTrue(policy["SUVerifyUpdateBeforeExtraction"])


if __name__ == "__main__":
    unittest.main()
