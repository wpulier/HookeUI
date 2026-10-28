// Signs one answer with the app's own key code (HookeUI/AgentShared.swift AgentConfirmKey) and prints the public key
// and signature as JSON, so services/tests/test_agent_confirm_native.py can check the server accepts exactly that.
import Foundation

let args = CommandLine.arguments
guard args.count == 4, let key = AgentConfirmKey(), let publicKey = key.publicBase64,
      let signature = key.sign(AgentConfirmKey.message(pending: args[1], code: args[2], answer: args[3])) else {
    FileHandle.standardError.write("usage: agent-confirm <pending id> <code> <answer>\n".data(using: .utf8)!)
    exit(2)
}
let out = try! JSONSerialization.data(withJSONObject: ["publicKey": publicKey, "signature": signature])
print(String(decoding: out, as: UTF8.self))
