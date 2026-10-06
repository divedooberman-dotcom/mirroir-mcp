// Copyright 2026 jfarcand@apache.org
// Licensed under the Apache License, Version 2.0
//
// ABOUTME: Registers the send_to_telegram tool: push text and/or a screenshot of the
// ABOUTME: mirrored iPhone to a FIXED Telegram recipient. Token is read from the macOS
// ABOUTME: Keychain at call time and never logged; the recipient cannot be set by the caller
// ABOUTME: (prompt-injection safe — a page that says "send this to X" cannot redirect it).

import Foundation
import HelperLib

extension MirroirMCP {

    /// Keychain service/account and recipient, all overridable by env but never by tool args.
    private static var telegramTokenName: String {
        ProcessInfo.processInfo.environment["MIRROIR_TELEGRAM_TOKEN_NAME"] ?? "TELEGRAM_BOT_TOKEN_DIGEST"
    }
    private static var telegramChatID: String {
        ProcessInfo.processInfo.environment["MIRROIR_TELEGRAM_CHAT_ID"] ?? "6171869559"
    }

    static func registerTelegramTools(
        server: MCPServer,
        registry: TargetRegistry
    ) {
        server.registerTool(MCPToolDefinition(
            name: "send_to_telegram",
            description: """
                Send a short text message and/or a screenshot of the mirrored iPhone to the \
                owner's own Telegram. The recipient is FIXED (configured by the operator, not \
                by this call) and cannot be changed — use this to surface something the owner \
                should see on their phone, never to message anyone else. Provide `text`, set \
                `screenshot` to true, or both. On success returns a confirmation; the bot token \
                is never exposed.
                """,
            inputSchema: [
                "type": .string("object"),
                "properties": .object([
                    "text": .object([
                        "type": .string("string"),
                        "description": .string("Plain-text message to send (optional)."),
                    ]),
                    "screenshot": .object([
                        "type": .string("boolean"),
                        "description": .string(
                            "If true, capture the current iPhone screen and send it as a photo."),
                    ]),
                    "caption": .object([
                        "type": .string("string"),
                        "description": .string("Caption for the screenshot (optional)."),
                    ]),
                ]),
            ],
            handler: { args in
                let text = args["text"]?.asString()
                let wantShot = args["screenshot"]?.asBool() ?? false
                let caption = args["caption"]?.asString()

                if (text == nil || text!.isEmpty) && !wantShot {
                    return .error("Nothing to send: provide `text`, set `screenshot` true, or both.")
                }

                guard let token = readKeychainSecret(
                    service: "apikey:\(telegramTokenName)", account: telegramTokenName
                ), !token.isEmpty else {
                    return .error("Telegram bot token '\(telegramTokenName)' not found in the macOS "
                                  + "Keychain. Add it with: security add-generic-password -s "
                                  + "'apikey:\(telegramTokenName)' -a '\(telegramTokenName)' -w")
                }

                var sent: [String] = []

                if let text, !text.isEmpty {
                    if let apiErr = telegramSendMessage(token: token, chatID: telegramChatID, text: text) {
                        return .error("Telegram message failed: \(apiErr)")  // never contains the token
                    }
                    sent.append("text")
                }

                if wantShot {
                    let (ctx, err) = registry.resolveForTool(args)
                    guard let ctx else { return err! }
                    guard let base64 = ctx.capture.captureSettledBase64(),
                          let png = Data(base64Encoded: base64) else {
                        return .error("Failed to capture a screenshot of the '\(ctx.name)' window.")
                    }
                    if let apiErr = telegramSendPhoto(
                        token: token, chatID: telegramChatID, png: png, caption: caption
                    ) {
                        return .error("Telegram photo failed: \(apiErr)")
                    }
                    sent.append("screenshot")
                }

                return .text("Sent to Telegram: \(sent.joined(separator: " + ")).")
            }
        ))
    }
}
