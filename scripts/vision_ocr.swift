import Foundation
import Vision
import AppKit

// One JSON object per image; coordinates normalized with a top-left origin.
struct Line: Codable {
    let text: String
    let confidence: Float
    let bbox: [Double]
}
struct Result: Codable {
    let image: String
    let revision: Int
    let lines: [Line]
}
for path in CommandLine.arguments.dropFirst() {
    do {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["en-US"]
        request.usesLanguageCorrection = true
        request.revision = 3
        let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path))
        try handler.perform([request])
        let lines = (request.results ?? []).compactMap { observation -> Line? in
            guard let candidate = observation.topCandidates(1).first else { return nil }
            let r = observation.boundingBox
            return Line(text: candidate.string, confidence: candidate.confidence,
                        bbox: [r.minX, 1-r.maxY, r.maxX, 1-r.minY])
        }.sorted { a, b in
            abs(a.bbox[1]-b.bbox[1]) < 0.003 ? a.bbox[0] < b.bbox[0] : a.bbox[1] < b.bbox[1]
        }
        let data = try JSONEncoder().encode(Result(image: path, revision: request.revision, lines: lines))
        print(String(data: data, encoding: .utf8)!)
    } catch {
        fputs("\(path): \(error)\n", stderr)
        exit(1)
    }
}
