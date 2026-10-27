// Checks a built release against one channel's update key (packaging/macos/README.md "Channels"): the appcast's own
// signature (SURequireSignedFeed) and the dmg's signature and length in its enclosure, both against the public half the
// channel's app trusts (channels.json's edPublicKey). A Beta signed with Stable's key, or the reverse, fails here
// before anything is published. Needs only CryptoKit:
//
//   swift packaging/macos/verify_update.swift <public-key-file> <appcast.xml> <dmg>
import CryptoKit
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data("verify_update: \(message)\n".utf8))
    exit(1)
}

func data(_ base64: String, _ what: String) -> Data {
    guard let value = Data(base64Encoded: base64.trimmingCharacters(in: .whitespacesAndNewlines)) else {
        fail("\(what) is not base64")
    }
    return value
}

/// The enclosure's sparkle:edSignature and length (the feed has one item per release).
final class Enclosure: NSObject, XMLParserDelegate {
    var signature: String?
    var length: String?
    var count = 0
    func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?,
                attributes: [String: String] = [:]) {
        guard name == "enclosure" else { return }
        count += 1
        signature = attributes["sparkle:edSignature"]
        length = attributes["length"]
    }
}

let args = CommandLine.arguments
guard args.count == 4 else { fail("usage: verify_update.swift <public-key-file> <appcast.xml> <dmg>") }
guard let keyText = try? String(contentsOfFile: args[1], encoding: .utf8) else { fail("can't read \(args[1])") }
guard let key = try? Curve25519.Signing.PublicKey(rawRepresentation: data(keyText, "the public key")) else {
    fail("\(args[1]) is not an EdDSA public key")
}
guard let feed = FileManager.default.contents(atPath: args[2]) else { fail("can't read \(args[2])") }
guard let dmg = FileManager.default.contents(atPath: args[3]) else { fail("can't read \(args[3])") }

// The feed's signature: sign_update appends it as a comment, signing the bytes before it.
guard let text = String(data: feed, encoding: .utf8),
      let block = text.range(of: "<!-- sparkle-signatures:") else { fail("the appcast is not signed") }
let fields = text[block.upperBound...]
guard let signatureLine = fields.split(separator: "\n").first(where: { $0.hasPrefix("edSignature:") }),
      let lengthLine = fields.split(separator: "\n").first(where: { $0.hasPrefix("length:") }),
      let signedLength = Int(lengthLine.dropFirst("length:".count).trimmingCharacters(in: .whitespaces)),
      signedLength <= feed.count
else { fail("the appcast's signature block is incomplete") }
let feedSignature = data(String(signatureLine.dropFirst("edSignature:".count)), "the appcast's signature")
guard key.isValidSignature(feedSignature, for: feed.prefix(signedLength)) else {
    fail("the appcast is not signed by this channel's key")
}

let enclosure = Enclosure()
let parser = XMLParser(data: feed)
parser.delegate = enclosure
guard parser.parse(), enclosure.count == 1, let signature = enclosure.signature else {
    fail("the appcast needs exactly one enclosure with sparkle:edSignature")
}
guard enclosure.length == String(dmg.count) else {
    fail("the enclosure says \(enclosure.length ?? "no") bytes; the dmg has \(dmg.count)")
}
guard key.isValidSignature(data(signature, "the enclosure's signature"), for: dmg) else {
    fail("the dmg is not signed by this channel's key")
}
print("verified: the appcast and \(URL(fileURLWithPath: args[3]).lastPathComponent) are signed by \(args[1])")
