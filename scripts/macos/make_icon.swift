import AppKit

// Strict, geometric icon for CodeAgents 2.x.
//
// Layout: solid black square (no rounded "card" inside — macOS will mask
// it into the system squircle). Three thick parallel diagonal strokes
// climb from the bottom-left corner toward the middle of the right
// edge. From top to bottom the strokes are orange (plan), blue (agent),
// green (ask). A heavy angular "CA" mark sits in the visual centre on
// top of the strokes.

let outputPath = CommandLine.arguments.dropFirst().first ?? "icon.png"
let size = CGSize(width: 1024, height: 1024)

let image = NSImage(size: size)
image.lockFocus()

let bounds = CGRect(origin: .zero, size: size)

// Pure black canvas. We deliberately do NOT round the corners ourselves —
// macOS applies its squircle mask, and keeping the source square avoids
// the "white square with a black tile inside" look the previous icon had.
NSColor.black.setFill()
NSBezierPath(rect: bounds).fill()

// Three diagonal strokes. They share the same direction vector and are
// offset perpendicular to it so they read as a parallel beam.
//
//   top    -> orange (plan)
//   middle -> blue   (agent)
//   bottom -> green  (ask)
//
// Geometry: each stroke originates somewhere along the bottom-left
// region and ends along the right edge near vertical centre. The angle
// works out to ~30° from horizontal which reads as the "60° from the
// bottom edge" the design called for.
let stripes: [NSColor] = [
    NSColor(srgbRed: 0.96, green: 0.62, blue: 0.20, alpha: 1.0),  // orange
    NSColor(srgbRed: 0.31, green: 0.63, blue: 1.00, alpha: 1.0),  // blue
    NSColor(srgbRed: 0.40, green: 0.85, blue: 0.45, alpha: 1.0),  // green
]
let strokeWidth: CGFloat = 70
let strokeGap: CGFloat = 36          // visual gap between strokes
let stride = strokeWidth + strokeGap

// Direction vector (left-bottom -> middle of right edge), normalised.
let dirX = size.width
let dirY = size.height * 0.5
let dirLen = (dirX * dirX + dirY * dirY).squareRoot()
let dx = dirX / dirLen
let dy = dirY / dirLen
// Perpendicular (rotated 90° CCW) so positive offsets push strokes "up".
let nx = -dy
let ny = dx

// The middle stroke runs corner -> middle of right edge. Top/bottom
// strokes are translated along the perpendicular by ±stride.
let baseStart = CGPoint(x: -40, y: -40)
let baseEnd = CGPoint(x: size.width + 40, y: size.height * 0.5)

for (idx, color) in stripes.enumerated() {
    // idx 0 (orange) -> +stride (top), idx 1 -> 0 (middle), idx 2 -> -stride.
    let offset = CGFloat(1 - idx) * stride
    let start = CGPoint(x: baseStart.x + nx * offset, y: baseStart.y + ny * offset)
    let end = CGPoint(x: baseEnd.x + nx * offset, y: baseEnd.y + ny * offset)
    let path = NSBezierPath()
    path.move(to: start)
    path.line(to: end)
    path.lineWidth = strokeWidth
    path.lineCapStyle = .butt
    color.setStroke()
    path.stroke()
}

// Heavy mono caps "CA" centred over the strokes. White on black + colour.
let style = NSMutableParagraphStyle()
style.alignment = .center
let mono = NSFont.monospacedSystemFont(ofSize: 460, weight: .heavy)
let titleAttributes: [NSAttributedString.Key: Any] = [
    .font: mono,
    .foregroundColor: NSColor.white,
    .paragraphStyle: style,
    .kern: -28.0,
]
let title = NSAttributedString(string: "CA", attributes: titleAttributes)
let titleSize = title.size()
let titleRect = CGRect(
    x: (size.width - titleSize.width) / 2,
    y: (size.height - titleSize.height) / 2 - 24,
    width: titleSize.width,
    height: titleSize.height
)
title.draw(in: titleRect)

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
