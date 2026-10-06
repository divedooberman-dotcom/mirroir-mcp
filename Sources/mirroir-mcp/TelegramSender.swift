// Copyright 2026 jfarcand@apache.org
// Licensed under the Apache License, Version 2.0
//
// ABOUTME: Keychain read + Telegram Bot API helpers for the send_to_telegram tool.
// ABOUTME: The bot token stays in-process (read from the Keychain, embedded only in the request
// ABOUTME: URL) and is never returned or logged: every error is reduced to a status code plus
// ABOUTME: Telegram's own JSON `description`, so a failure cannot leak the token.

import Foundation

/// Read a generic-password secret from the macOS Keychain via `security`.
/// The value is returned on stdout (in-process); it never appears in this process's argv.
func readKeychainSecret(service: String, account: String) -> String? {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
    process.arguments = ["find-generic-password", "-s", service, "-a", account, "-w"]
    let stdout = Pipe()
    process.standardOutput = stdout
    process.standardError = Pipe()
    do {
        try process.run()
    } catch {
        return nil
    }
    let data = stdout.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    guard process.terminationStatus == 0 else { return nil }
    let value = String(data: data, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines)
    return (value?.isEmpty == false) ? value : nil
}

/// POST a request and return a SANITIZED error string, or nil on success (HTTP 2xx).
/// Never includes the request URL (which carries the token) — only a status code and, when the
/// body is Telegram's JSON, its `description` field.
private func telegramPOST(url: URL, body: Data, contentType: String) -> String? {
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.httpBody = body
    request.timeoutInterval = 20
    request.setValue(contentType, forHTTPHeaderField: "Content-Type")

    let semaphore = DispatchSemaphore(value: 0)
    nonisolated(unsafe) var responseData: Data?
    nonisolated(unsafe) var httpStatus: Int?
    nonisolated(unsafe) var hadError = false

    let task = URLSession.shared.dataTask(with: request) { data, response, error in
        responseData = data
        httpStatus = (response as? HTTPURLResponse)?.statusCode
        hadError = (error != nil)
        semaphore.signal()
    }
    task.resume()

    if semaphore.wait(timeout: .now() + .seconds(25)) == .timedOut {
        task.cancel()
        return "network timeout (token not disclosed)"
    }
    if hadError {
        // Deliberately drop the NSError: its userInfo can contain the failing URL + token.
        return "network error contacting Telegram (token not disclosed)"
    }
    if let status = httpStatus, (200...299).contains(status) {
        return nil
    }
    var desc = ""
    if let data = responseData,
       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
       let d = obj["description"] as? String {
        desc = d
    }
    return "API error \(httpStatus ?? 0)\(desc.isEmpty ? "" : ": \(desc)")"
}

/// Send a plain-text message. Returns nil on success, a sanitized error otherwise.
func telegramSendMessage(token: String, chatID: String, text: String) -> String? {
    guard let url = URL(string: "https://api.telegram.org/bot\(token)/sendMessage") else {
        return "bad Telegram URL"
    }
    var components = URLComponents()
    components.queryItems = [
        URLQueryItem(name: "chat_id", value: chatID),
        URLQueryItem(name: "text", value: text),
        URLQueryItem(name: "disable_web_page_preview", value: "true"),
    ]
    let body = Data((components.percentEncodedQuery ?? "").utf8)
    return telegramPOST(url: url, body: body,
                        contentType: "application/x-www-form-urlencoded")
}

/// Send a PNG as a photo (multipart/form-data). Returns nil on success, a sanitized error otherwise.
func telegramSendPhoto(token: String, chatID: String, png: Data, caption: String?) -> String? {
    guard let url = URL(string: "https://api.telegram.org/bot\(token)/sendPhoto") else {
        return "bad Telegram URL"
    }
    let boundary = "mirroir-\(UUID().uuidString)"
    var body = Data()

    func field(_ name: String, _ value: String) {
        body.append(Data("--\(boundary)\r\n".utf8))
        body.append(Data("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".utf8))
        body.append(Data("\(value)\r\n".utf8))
    }

    field("chat_id", chatID)
    if let caption, !caption.isEmpty { field("caption", caption) }
    body.append(Data("--\(boundary)\r\n".utf8))
    body.append(Data("Content-Disposition: form-data; name=\"photo\"; filename=\"screen.png\"\r\n".utf8))
    body.append(Data("Content-Type: image/png\r\n\r\n".utf8))
    body.append(png)
    body.append(Data("\r\n--\(boundary)--\r\n".utf8))

    return telegramPOST(url: url, body: body,
                        contentType: "multipart/form-data; boundary=\(boundary)")
}
