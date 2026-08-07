// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "HermesG2Summary",
    platforms: [.macOS(.v26)],
    products: [.executable(name: "hermes-g2-summary", targets: ["HermesG2Summary"])],
    targets: [.executableTarget(name: "HermesG2Summary")]
)
