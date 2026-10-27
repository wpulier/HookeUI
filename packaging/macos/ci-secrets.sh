#!/bin/bash
# Once: give .github/workflows/mac-app.yml what it needs to release HookeUI (packaging/macos/README.md "From CI").
#
# Before it, in Keychain Access (login > My Certificates): right-click "Developer ID Application: WILLIAM PULIER
# (SY7TABCD5M)" > Export, save it as ~/Desktop/DeveloperID.p12 with a password. Then, in Terminal:
#
#   packaging/macos/ci-secrets.sh
#
# It asks for that password, the App Store Connect key to notarize with, and a GitHub token that may publish releases.
# Every value goes straight from here into GitHub's encrypted secrets; nothing is printed or kept.
set -euo pipefail
REPO="${REPO:-wpulier/HookeUI}"
P12="${P12:-$HOME/Desktop/DeveloperID.p12}"
put() { gh secret set "$1" -R "$REPO" >/dev/null; echo "  set $1"; }

[ -f "$P12" ] || { echo "No $P12: export the Developer ID certificate from Keychain Access first (see the top of this file)." >&2; exit 1; }
read -r -s -p "Password you gave DeveloperID.p12: " p12pass; echo
openssl pkcs12 -in "$P12" -passin "pass:$p12pass" -nokeys -legacy 2>/dev/null | grep -q "Developer ID Application" \
  || openssl pkcs12 -in "$P12" -passin "pass:$p12pass" -nokeys 2>/dev/null | grep -q "Developer ID Application" \
  || { echo "That file or password isn't the Developer ID Application certificate." >&2; exit 1; }

keys=(~/Downloads/AuthKey_*.p8)
echo "App Store Connect key to notarize with:"
select key in "${keys[@]}"; do [ -n "$key" ] && break; done
keyid="$(basename "$key" .p8)"; keyid="${keyid#AuthKey_}"
read -r -p "Its Issuer ID (Users and Access > Integrations): " issuer

echo "A GitHub token that may publish releases on wpulier/hookeui.org: make a fine-grained token at"
echo "  https://github.com/settings/personal-access-tokens/new  (Repository access: wpulier/hookeui.org only;"
echo "  Permissions: Contents, Read and write), then paste it."
read -r -s -p "Token: " token; echo

echo "Setting the secrets on $REPO:"
base64 -i "$P12" | put MACOS_CERTIFICATE
printf '%s' "$p12pass" | put MACOS_CERTIFICATE_PASSWORD
put NOTARY_API_KEY <"$key"
printf '%s' "$keyid" | put NOTARY_API_KEY_ID
printf '%s' "$issuer" | put NOTARY_API_ISSUER
put SPARKLE_ED_KEY <"$HOME/.config/hookeui-release/sparkle_ed25519.key"
printf '%s' "$token" | put HOOKEUI_RELEASE_TOKEN
unset p12pass token
rm -P "$P12" 2>/dev/null || rm -f "$P12"
echo "Done: $P12 deleted. Prove it with Actions > mac-app > Run workflow (publish off), or ship with a tag app-v<version>."
