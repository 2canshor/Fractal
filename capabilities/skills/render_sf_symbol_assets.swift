#!/usr/bin/env swift

import AppKit
import CryptoKit
import Foundation

enum SymbolAssetError: Error, CustomStringConvertible {
    case message(String)

    var description: String {
        switch self {
        case let .message(value): value
        }
    }
}

func argument(_ name: String) throws -> String {
    guard let index = CommandLine.arguments.firstIndex(of: name),
          CommandLine.arguments.indices.contains(index + 1)
    else {
        throw SymbolAssetError.message("Missing required argument: \(name)")
    }
    return CommandLine.arguments[index + 1]
}

func optionalArgument(_ name: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: name),
          CommandLine.arguments.indices.contains(index + 1)
    else {
        return nil
    }
    return CommandLine.arguments[index + 1]
}

func jsonObject(at url: URL) throws -> [String: Any] {
    let data = try Data(contentsOf: url)
    guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw SymbolAssetError.message("Expected a JSON object at \(url.path)")
    }
    return value
}

func fixedColor(hex: String) throws -> NSColor {
    let value = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
    guard value.count == 6, let number = Int(value, radix: 16) else {
        throw SymbolAssetError.message("Invalid RGB colour: \(hex)")
    }
    return NSColor(
        calibratedRed: CGFloat((number >> 16) & 0xff) / 255,
        green: CGFloat((number >> 8) & 0xff) / 255,
        blue: CGFloat(number & 0xff) / 255,
        alpha: 1
    )
}

func sha256(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func renderSymbol(
    name: String,
    foregroundColor: NSColor,
    outerColor: NSColor,
    pixels: Int
) throws -> Data {
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: pixels,
        pixelsHigh: pixels,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bitmapFormat: [],
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        throw SymbolAssetError.message("Could not create a \(pixels)-pixel bitmap")
    }
    bitmap.size = NSSize(width: pixels, height: pixels)
    guard let graphics = NSGraphicsContext(bitmapImageRep: bitmap) else {
        throw SymbolAssetError.message("Could not create an AppKit graphics context")
    }

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = graphics
    graphics.imageInterpolation = .high
    NSColor.clear.setFill()
    NSRect(x: 0, y: 0, width: pixels, height: pixels).fill()

    let pointSize = CGFloat(pixels) * 0.72
    let baseConfiguration = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .regular)
    let paletteConfiguration = NSImage.SymbolConfiguration(
        paletteColors: [foregroundColor, outerColor]
    )
    let configuration = baseConfiguration.applying(paletteConfiguration)
    guard let image = NSImage(
        systemSymbolName: name,
        accessibilityDescription: nil
    )?.withSymbolConfiguration(configuration) else {
        NSGraphicsContext.restoreGraphicsState()
        throw SymbolAssetError.message("AppKit could not render SF Symbol: \(name)")
    }

    let padding = CGFloat(pixels) * 0.125
    let available = NSSize(
        width: CGFloat(pixels) - (padding * 2),
        height: CGFloat(pixels) - (padding * 2)
    )
    let scale = min(available.width / image.size.width, available.height / image.size.height)
    let renderedSize = NSSize(width: image.size.width * scale, height: image.size.height * scale)
    let destination = NSRect(
        x: (CGFloat(pixels) - renderedSize.width) / 2,
        y: (CGFloat(pixels) - renderedSize.height) / 2,
        width: renderedSize.width,
        height: renderedSize.height
    )
    image.draw(
        in: destination,
        from: .zero,
        operation: .sourceOver,
        fraction: 1,
        respectFlipped: true,
        hints: [.interpolation: NSImageInterpolation.high]
    )
    graphics.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()

    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        throw SymbolAssetError.message("Could not encode SF Symbol as PNG: \(name)")
    }
    return png
}

func renderContactSheet(
    symbols: [[String: Any]],
    skillsRoot: URL,
    dark: Bool,
    destination: URL
) throws {
    let sizes = [16, 20, 24, 32]
    let width = 900
    let height = 170 + (symbols.count * 65)
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: width,
        pixelsHigh: height,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bitmapFormat: [],
        bytesPerRow: 0,
        bitsPerPixel: 0
    ), let graphics = NSGraphicsContext(bitmapImageRep: bitmap) else {
        throw SymbolAssetError.message("Could not create a contact-sheet bitmap")
    }
    bitmap.size = NSSize(width: width, height: height)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = graphics
    let background = dark
        ? NSColor(calibratedWhite: 0.10, alpha: 1)
        : NSColor(calibratedWhite: 0.97, alpha: 1)
    let foreground = dark ? NSColor.white : NSColor.black
    let secondary = dark
        ? NSColor(calibratedWhite: 0.68, alpha: 1)
        : NSColor(calibratedWhite: 0.35, alpha: 1)
    background.setFill()
    NSRect(x: 0, y: 0, width: width, height: height).fill()
    let titleAttributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 22, weight: .semibold),
        .foregroundColor: foreground,
    ]
    let labelAttributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 16, weight: .medium),
        .foregroundColor: foreground,
    ]
    let detailAttributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
        .foregroundColor: secondary,
    ]
    let appearance = dark ? "Dark" : "Light"
    ("\(appearance) · checked-in PNG assets" as NSString).draw(
        at: NSPoint(x: 36, y: height - 45),
        withAttributes: titleAttributes
    )
    for (index, size) in sizes.enumerated() {
        ("\(size) px" as NSString).draw(
            at: NSPoint(x: 360 + (index * 125), y: height - 90),
            withAttributes: labelAttributes
        )
    }
    for (row, symbol) in symbols.enumerated() {
        guard let entryID = symbol["entry_id"] as? String,
              let interfaceType = symbol["interface_type"] as? String
        else {
            NSGraphicsContext.restoreGraphicsState()
            throw SymbolAssetError.message("Contact-sheet symbol metadata is invalid")
        }
        let imageURL = skillsRoot
            .appendingPathComponent(entryID, isDirectory: true)
            .appendingPathComponent("assets/\(entryID)-small.png")
        guard let image = NSImage(contentsOf: imageURL) else {
            NSGraphicsContext.restoreGraphicsState()
            throw SymbolAssetError.message("Could not load contact-sheet asset: \(entryID)")
        }
        let y = height - 155 - (row * 65)
        (entryID.capitalized as NSString).draw(
            at: NSPoint(x: 36, y: y + 12),
            withAttributes: labelAttributes
        )
        (interfaceType as NSString).draw(
            at: NSPoint(x: 180, y: y + 14),
            withAttributes: detailAttributes
        )
        for (column, size) in sizes.enumerated() {
            let dimension = CGFloat(size)
            let x = CGFloat(375 + (column * 125))
            image.draw(
                in: NSRect(
                    x: x - (dimension / 2),
                    y: CGFloat(y + 4),
                    width: dimension,
                    height: dimension
                ),
                from: .zero,
                operation: .sourceOver,
                fraction: 1,
                respectFlipped: true,
                hints: [.interpolation: NSImageInterpolation.high]
            )
        }
    }
    graphics.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        throw SymbolAssetError.message("Could not encode contact sheet")
    }
    try png.write(to: destination, options: .atomic)
}

func main() throws {
    let policyURL = URL(fileURLWithPath: try argument("--policy"))
    let skillsRoot = URL(fileURLWithPath: try argument("--skills-root"), isDirectory: true)
    let sfSymbolsApp = URL(fileURLWithPath: try argument("--sf-symbols-app"), isDirectory: true)
    let manifestURL = URL(fileURLWithPath: try argument("--manifest"))

    let policy = try jsonObject(at: policyURL)
    guard policy["record_type"] as? String == "user-surface-policy",
          policy["record_version"] as? Int == 2,
          let entries = policy["entries"] as? [[String: Any]]
    else {
        throw SymbolAssetError.message("The renderer requires user-surface-policy record v2")
    }

    let availabilityURL = sfSymbolsApp
        .appendingPathComponent("Contents/Resources/Metadata/name_availability.plist")
    let availabilityData = try Data(contentsOf: availabilityURL)
    guard let availability = try PropertyListSerialization.propertyList(
        from: availabilityData,
        format: nil
    ) as? [String: Any],
        let availableSymbols = availability["symbols"] as? [String: String]
    else {
        throw SymbolAssetError.message("Could not read SF Symbols name availability metadata")
    }

    let infoURL = sfSymbolsApp.appendingPathComponent("Contents/Info.plist")
    let infoData = try Data(contentsOf: infoURL)
    guard let info = try PropertyListSerialization.propertyList(
        from: infoData,
        format: nil
    ) as? [String: Any],
        let appVersion = info["CFBundleShortVersionString"] as? String
    else {
        throw SymbolAssetError.message("Could not read the SF Symbols app version")
    }

    let actionColorHex = "#0A84FF"
    let commandColorHex = "#BF5AF2"
    let actionColor = try fixedColor(hex: actionColorHex)
    let commandColor = try fixedColor(hex: commandColorHex)
    let fileManager = FileManager.default
    var seenNames = Set<String>()
    var seenEntries = Set<String>()
    var manifestSymbols: [[String: Any]] = []

    for entry in entries.sorted(by: {
        ($0["entry_id"] as? String ?? "") < ($1["entry_id"] as? String ?? "")
    }) {
        guard let entryID = entry["entry_id"] as? String,
              let interfaceType = entry["interface_type"] as? String,
              let symbol = entry["symbol"] as? [String: Any],
              symbol["system"] as? String == "sf-symbols",
              let symbolName = symbol["name"] as? String,
              let selection = symbol["selection"] as? [String: Any],
              let rationale = selection["rationale"] as? String,
              let searchTerms = selection["search_terms"] as? [String],
              let alternatives = selection["alternatives_considered"] as? [String]
        else {
            throw SymbolAssetError.message("Every entry requires an evidenced SF Symbol mapping")
        }
        guard seenEntries.insert(entryID).inserted else {
            throw SymbolAssetError.message("Duplicate user-surface entry: \(entryID)")
        }
        guard seenNames.insert(symbolName).inserted else {
            throw SymbolAssetError.message("Duplicate SF Symbol identifier: \(symbolName)")
        }
        guard let introduced = availableSymbols[symbolName] else {
            throw SymbolAssetError.message("Unknown SF Symbol identifier: \(symbolName)")
        }
        guard rationale.count >= 40,
              searchTerms.count >= 2,
              Set(searchTerms).count == searchTerms.count,
              !alternatives.isEmpty,
              Set(alternatives).count == alternatives.count,
              !alternatives.contains(symbolName)
        else {
            throw SymbolAssetError.message("SF Symbol selection evidence is incomplete: \(entryID)")
        }
        for alternative in alternatives where availableSymbols[alternative] == nil {
            throw SymbolAssetError.message(
                "Unknown alternative SF Symbol for \(entryID): \(alternative)"
            )
        }
        let containerShape: String
        let paletteName: String
        let outerColor: NSColor
        let outerColorHex: String
        let foregroundColor: NSColor
        let foregroundColorHex: String
        switch interfaceType {
        case "action":
            containerShape = "circle"
            paletteName = "action"
            outerColor = actionColor
            outerColorHex = actionColorHex
            foregroundColor = NSColor.white
            foregroundColorHex = "#FFFFFF"
            guard symbolName.contains(".circle") else {
                throw SymbolAssetError.message("Action symbol is not circle-contained: \(entryID)")
            }
        case "command":
            containerShape = "square"
            outerColor = commandColor
            outerColorHex = commandColorHex
            if symbolName.hasSuffix(".fill") {
                paletteName = "command"
                foregroundColor = NSColor.white
                foregroundColorHex = "#FFFFFF"
            } else {
                paletteName = "command-outline"
                foregroundColor = commandColor
                foregroundColorHex = commandColorHex
            }
            guard symbolName.contains(".square") else {
                throw SymbolAssetError.message("Command symbol is not square-contained: \(entryID)")
            }
        default:
            throw SymbolAssetError.message("Unknown interface type for \(entryID): \(interfaceType)")
        }

        let assetsDirectory = skillsRoot
            .appendingPathComponent(entryID, isDirectory: true)
            .appendingPathComponent("assets", isDirectory: true)
        try fileManager.createDirectory(
            at: assetsDirectory,
            withIntermediateDirectories: true
        )
        var assets: [String: Any] = [:]
        for (sizeName, pixels) in [("small", 400), ("large", 800)] {
            let fileName = "\(entryID)-\(sizeName).png"
            let data = try renderSymbol(
                name: symbolName,
                foregroundColor: foregroundColor,
                outerColor: outerColor,
                pixels: pixels
            )
            let destination = assetsDirectory.appendingPathComponent(fileName)
            try data.write(to: destination, options: .atomic)
            assets[sizeName] = [
                "path": "capabilities/skills/\(entryID)/assets/\(fileName)",
                "openai_path": "./assets/\(fileName)",
                "pixels": pixels,
                "sha256": sha256(data),
            ]
        }
        manifestSymbols.append([
            "entry_id": entryID,
            "interface_type": interfaceType,
            "name": symbolName,
            "container_shape": containerShape,
            "rendering": "palette",
            "palette": paletteName,
            "outer_color": outerColorHex,
            "foreground_color": foregroundColorHex,
            "sf_symbols_introduced": introduced,
            "selection": selection,
            "assets": assets,
        ])
    }

    guard !manifestSymbols.isEmpty else {
        throw SymbolAssetError.message("The user surface requires at least one entry")
    }
    let actionCount = manifestSymbols.filter({
        $0["interface_type"] as? String == "action"
    }).count
    let commandCount = manifestSymbols.filter({
        $0["interface_type"] as? String == "command"
    }).count

    let manifest: [String: Any] = [
        "record_type": "user-surface-symbol-manifest",
        "record_version": 1,
        "symbol_system": "sf-symbols",
        "sf_symbols_app_version": appVersion,
        "palettes": [
            "action": ["outer_color": actionColorHex, "foreground_color": "#FFFFFF"],
            "command": ["outer_color": commandColorHex, "foreground_color": "#FFFFFF"],
            "command-outline": [
                "outer_color": commandColorHex,
                "foreground_color": commandColorHex,
            ],
        ],
        "summary": [
            "entry_count": manifestSymbols.count,
            "action_count": actionCount,
            "command_count": commandCount,
        ],
        "verification_contract": [
            "required_sizes_px": [16, 20, 24, 32],
            "required_appearances": ["light", "dark"],
            "codex_discovery_order": ["plugin/installed", "skills/list:forceReload"],
            "live_ui_required_after_install": true,
        ],
        "symbols": manifestSymbols,
    ]
    let manifestData = try JSONSerialization.data(
        withJSONObject: manifest,
        options: [.prettyPrinted, .sortedKeys]
    ) + Data([0x0a])
    try fileManager.createDirectory(
        at: manifestURL.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    try manifestData.write(to: manifestURL, options: .atomic)
    if let contactSheetPath = optionalArgument("--contact-sheet-dir") {
        let contactSheetDirectory = URL(
            fileURLWithPath: contactSheetPath,
            isDirectory: true
        )
        try fileManager.createDirectory(
            at: contactSheetDirectory,
            withIntermediateDirectories: true
        )
        try renderContactSheet(
            symbols: manifestSymbols,
            skillsRoot: skillsRoot,
            dark: false,
            destination: contactSheetDirectory.appendingPathComponent(
                "user-surface-symbols-light.png"
            )
        )
        try renderContactSheet(
            symbols: manifestSymbols,
            skillsRoot: skillsRoot,
            dark: true,
            destination: contactSheetDirectory.appendingPathComponent(
                "user-surface-symbols-dark.png"
            )
        )
        print("Contact sheets: \(contactSheetDirectory.path)")
    }
    print("Rendered \(manifestSymbols.count) SF Symbols with SF Symbols \(appVersion)")
    print("Manifest: \(manifestURL.path)")
}

do {
    try main()
} catch {
    FileHandle.standardError.write(Data("error: \(error)\n".utf8))
    exit(1)
}
