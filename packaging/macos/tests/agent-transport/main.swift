// Exercise the same JSON/download transport as the CLI; no account or Keychain access.
import Foundation
let mode = CommandLine.arguments[1]
var request = URLRequest(url: URL(string: CommandLine.arguments[2])!)
request.setValue("Bearer synthetic-test-credential", forHTTPHeaderField: "Authorization")
request.setValue("synthetic-owner-window", forHTTPHeaderField: "X-Hooke-Studio")
request.httpMethod = mode == "data" ? "POST" : "GET"
if mode == "data" { request.httpBody = Data("synthetic-private-request".utf8) }
let transport = AgentDoorTransport.session()
let done = DispatchSemaphore(value: 0)
func complete(_ response: URLResponse?, _ error: Error?) {
    print((response as? HTTPURLResponse)?.statusCode ?? 0)
    if let error { print(error.localizedDescription) }
    done.signal()
}
if mode == "data" {
    transport.dataTask(with: request) { _, response, error in complete(response, error) }.resume()
} else {
    transport.downloadTask(with: request) { _, response, error in complete(response, error) }.resume()
}
if done.wait(timeout: .now() + 10) == .timedOut { fatalError("Transport did not settle") }
transport.invalidateAndCancel()
