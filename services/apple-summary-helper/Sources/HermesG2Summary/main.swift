import Foundation
import FoundationModels

@Generable
struct TurnSummary: Codable {
    @Guide(description: "A concise outcome headline, under 12 words")
    var headline: String
    @Guide(description: "What the agent actually accomplished")
    var outcome: String
    @Guide(description: "The most important changes, under 30 words")
    var keyChanges: String
    @Guide(description: "Tests or validation performed, or Not verified")
    var validation: String
    @Guide(description: "The remaining blocker, or None")
    var blocker: String
    @Guide(description: "One concrete next action")
    var suggestedNextAction: String
}

@main
struct HermesG2SummaryCommand {
    static func main() async throws {
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let answer = String(data: input, encoding: .utf8), !answer.isEmpty else {
            throw SummaryError.emptyInput
        }
        let session = LanguageModelSession(instructions: """
            Summarize a coding agent's completed response for a 576 by 288 monochrome glasses display.
            Preserve concrete outcomes, files, tests, failures, and next actions. Never invent validation.
            """)
        let response = try await session.respond(
            to: String(answer.prefix(12_000)),
            generating: TurnSummary.self
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        FileHandle.standardOutput.write(try encoder.encode(response.content))
    }
}

enum SummaryError: Error {
    case emptyInput
}
