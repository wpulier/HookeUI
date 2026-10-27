# 23. Blackmagic is the default camera, and only writes proven on the phone reach it

**Context.**
- **The iPhone through Continuity Camera** gives a smooth picture with nothing to set up. But the Mac can't hold the
  phone's focus or exposure (Continuity Camera tells it nothing about the lens), and the phone hunts on a slide.
- **Blackmagic Camera** (free, on the phone) streams to the Mac and records on the phone in full quality. Its focus,
  exposure and colour can be held through its own REST API, and it has the phone's lenses.
- **That API broke the app again and again** on 2026-10-11 and 12:
  - a custom server URL and a quality change each stopped its HTTP server;
  - a server it didn't list left its stream "Interrupted";
  - reading its colour settings crashed it;
  - every streaming profile of ours whose resolutions were named HD or 4K crashed it as its stream started, and then
    on every open.

**Decision.**
- **Blackmagic is the default live source** (Will, 2026-10-12). The iPhone stays one press away.
- **Its link is never the user's choice.** The bridge streams over the cable when it shares a network, otherwise over
  Wi-Fi, and moves the phone when that changes. The pinned Wi-Fi and USB sources were removed. Choosing a link made
  people learn Personal Hotspot.
- **Only writes proven on the phone are sent:**
  - **The manifest:** `blackmagic-api.json` records, per app version, what the Phone check found safe. `Phone.put`
    refuses everything else. The exceptions are streaming's start, stop and server choice, which the picture needs on
    any version.
  - **How the check proves a write:** it tries each write once (the same value, then a small change and back). After
    each it asks whether the app is still there and reads the phone's crash reports, and it never retries what crashed.
  - **After an app update,** nothing is proven for the new version until the check runs again. The picture keeps
    working meanwhile.
- **A crash teaches:** a crash report within 30 s of the bridge starting the stream marks that quality unsafe for
  good, and the bridge never starts the stream at it again.
- **The settings file offers one quality**, Standard, in Blackmagic's own form (2160p, 1080p, 720p). It is imported
  once by hand from a QR code, since the app ignores files sent over the network.

**Cost.**
- **First-time setup has three steps** on the phone that the Mac can't do for it (HTTP Server on, the import, the
  platform and quality).
- **The first Phone check takes about 10 minutes** with the app kept open, and the check runs again after each app
  update.
- **Wi-Fi bunches frames** more than the cable or the iPhone ("slight stutter").
- **Photo is the Mac's:** the API has no still-photo route, so it saves the stream's frame.
