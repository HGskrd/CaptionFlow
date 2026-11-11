// swift-tools-version: 5.10
import PackageDescription
let package = Package(
    name: "CaptionFlow",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "CaptionFlow", targets: ["CaptionFlow"])],
    targets: [
        .target(name: "CaptionAudio", path: "native/CaptionAudio", exclude: ["vendor/COPYING", "vendor/README.txt"], publicHeadersPath: "include", cSettings: [.define("HAVE_CONFIG_H"), .headerSearchPath("vendor")]),
        .executableTarget(name: "CaptionFlow", dependencies: ["CaptionAudio"], resources: [.copy("Resources/worker")]),
        .testTarget(name: "CaptionFlowTests", dependencies: ["CaptionFlow"])
    ]
)
