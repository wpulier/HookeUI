// Signs one answer with the app's own key code (HookeUI/AgentShared.swift AgentConfirmKey) and prints the public key
// and signature as JSON, so services/tests/test_agent_confirm_native.py can check the server accepts exactly that.
import Foundation

let args = CommandLine.arguments
guard [4, 6].contains(args.count), let key = AgentConfirmKey(), let publicKey = key.publicBase64,
      let signature = key.sign(args.count == 6
        ? AgentConfirmKey.projectGrant(id: args[2], session: args[3], connection: args[4], role: args[5], cancel: args[1] == "grant-cancel")
        : AgentConfirmKey.message(pending: args[1], code: args[2], answer: args[3])) else {
    FileHandle.standardError.write("usage: agent-confirm <pending id> <code> <answer>\n".data(using: .utf8)!)
    exit(2)
}
let out = try! JSONSerialization.data(withJSONObject: ["publicKey": publicKey, "signature": signature])
print(String(decoding: out, as: UTF8.self))
