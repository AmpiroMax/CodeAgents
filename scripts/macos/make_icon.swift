import AppKit

let outputPath = CommandLine.arguments.dropFirst().first ?? "icon.png"
let size = CGSize(width: 1024, height: 1024)

let image = NSImage(size: size)
image.lockFocus()

let bounds = CGRect(origin: .zero, size: size)
let radius: CGFloat = 220
let path = NSBezierPath(roundedRect: bounds, xRadius: radius, yRadius: radius)

let topColor = NSColor(calibratedRed: 0.10, green: 0.18, blue: 0.36, alpha: 1.0)
let bottomColor = NSColor(calibratedRed: 0.04, green: 0.07, blue: 0.16, alpha: 1.0)
let gradient = NSGradient(starting: topColor, ending: bottomColor)!
path.addClip()
gradient.draw(in: bounds, angle: 270)

let glowRect = CGRect(x: -200, y: 350, width: 1400, height: 800)
let glowPath = NSBezierPath(ovalIn: glowRect)
NSColor(calibratedRed: 0.35, green: 0.55, blue: 1.0, alpha: 0.18).setFill()
glowPath.fill()

let style = NSMutableParagraphStyle()
style.alignment = .center

let titleFont = NSFont.systemFont(ofSize: 460, weight: .heavy)
let titleAttributes: [NSAttributedString.Key: Any] = [
    .font: titleFont,
    .foregroundColor: NSColor.white,
    .paragraphStyle: style,
    .kern: -8.0,
]
let title = NSAttributedString(string: "CA", attributes: titleAttributes)
let titleSize = title.size()
let titleRect = CGRect(
    x: (size.width - titleSize.width) / 2,
    y: (size.height - titleSize.height) / 2 + 40,
    width: titleSize.width,
    height: titleSize.height
)
title.draw(in: titleRect)

let subtitleFont = NSFont.systemFont(ofSize: 96, weight: .semibold)
let subtitleAttributes: [NSAttributedString.Key: Any] = [
    .font: subtitleFont,
    .foregroundColor: NSColor(calibratedWhite: 1.0, alpha: 0.78),
    .paragraphStyle: style,
    .kern: 4.0,
]
let subtitle = NSAttributedString(string: "SERVICES", attributes: subtitleAttributes)
let subtitleSize = subtitle.size()
let subtitleRect = CGRect(
    x: (size.width - subtitleSize.width) / 2,
    y: 130,
    width: subtitleSize.width,
    height: subtitleSize.height
)
subtitle.draw(in: subtitleRect)

image.unlockFocus()

guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:])
else {
    FileHandle.standardError.write("failed to render icon\n".data(using: .utf8)!)
    exit(1)
}

let url = URL(fileURLWithPath: outputPath)
try png.write(to: url)
