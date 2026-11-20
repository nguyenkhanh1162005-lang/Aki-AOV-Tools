import Foundation

struct LatestRelease: Decodable, Sendable {
    let tag_name: String
    let html_url: URL
    let name: String?
    let body: String?
}

enum UpdateServiceError: LocalizedError {
    case noRelease
    case badResponse

    var errorDescription: String? {
        switch self {
        case .noRelease:
            return "Chưa có bản IPA phát hành công khai trên GitHub."
        case .badResponse:
            return "Không đọc được thông tin cập nhật từ GitHub."
        }
    }
}

enum UpdateService {
    static func latestRelease() async throws -> LatestRelease {
        let url = URL(string: "https://api.github.com/repos/nguyenkhanh1162005-lang/Aki-AOV-Tools/releases/latest")!
        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("AOVSkinMerger-iOS", forHTTPHeaderField: "User-Agent")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw UpdateServiceError.badResponse
        }
        if http.statusCode == 404 {
            throw UpdateServiceError.noRelease
        }
        guard (200..<300).contains(http.statusCode) else {
            throw UpdateServiceError.badResponse
        }
        return try JSONDecoder().decode(LatestRelease.self, from: data)
    }
}
