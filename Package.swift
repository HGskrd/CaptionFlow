// swift-tools-version: 5.10
import PackageDescription
let package = Package(
    name: "CaptionFlow",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "CaptionFlow", targets: ["CaptionFlow"])],
    targets: [
        .executableTarget(name: "CaptionFlow", resources: [.copy("Resources/worker")]),
        .testTarget(name: "CaptionFlowTests", dependencies: ["CaptionFlow"])
    ]
)
