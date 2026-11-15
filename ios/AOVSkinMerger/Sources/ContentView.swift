import SwiftUI
import UniformTypeIdentifiers
import UIKit

struct ContentView: View {
    @Environment(\.openURL) private var openURL
    @State private var isImporting = false
    @State private var isAnalyzing = false
    @State private var selectedURLs: [URL] = []
    @State private var analysis: ArchiveAnalysis?
    @State private var alertText = ""
    @State private var showAlert = false
    @State private var updateMessage = "Chưa kiểm tra cập nhật"
    @State private var releaseURL: URL?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Nguồn MOD", systemImage: "doc.zipper").font(.headline)
                        Text("Chọn nhiều ZIP. File được giữ trên iPhone, không tải lên máy chủ.")
                            .font(.footnote).foregroundColor(.secondary)
                        Button { isImporting = true } label: {
                            Label("Chọn file ZIP", systemImage: "plus.circle.fill")
                                .frame(maxWidth: .infinity)
                        }.buttonStyle(.borderedProminent)
                        Button(action: runAnalysis) {
                            HStack {
                                if isAnalyzing { ProgressView().tint(.white) }
                                Label(isAnalyzing ? "Đang kiểm tra…" : "Kiểm tra cấu trúc ZIP",
                                      systemImage: "doc.text.magnifyingglass")
                            }.frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .disabled(selectedURLs.isEmpty || isAnalyzing)
                    }
                    .panel()

                    if !selectedURLs.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("File đã chọn").font(.headline)
                                Spacer()
                                Text("\(selectedURLs.count) ZIP").font(.caption).foregroundColor(.secondary)
                            }
                            ForEach(Array(selectedURLs.enumerated()), id: \.offset) { index, url in
                                HStack {
                                    Image(systemName: "doc.zipper").foregroundColor(.accentColor)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(url.lastPathComponent).font(.subheadline).lineLimit(1)
                                        Text(fileSize(url)).font(.caption).foregroundColor(.secondary)
                                    }
                                    Spacer()
                                    Button(role: .destructive) {
                                        selectedURLs.remove(at: index)
                                        analysis = nil
                                    } label: { Image(systemName: "xmark.circle.fill") }
                                    .buttonStyle(.plain)
                                }
                            }
                            Button("Xóa danh sách", role: .destructive) {
                                selectedURLs.removeAll()
                                analysis = nil
                            }.font(.footnote)
                        }.panel()
                    }

                    if let analysis { analysisView(analysis) }

                    VStack(alignment: .leading, spacing: 9) {
                        Label("Tiến độ phát triển", systemImage: "hammer.fill").font(.headline)
                        statusRow("Nhập nhiều ZIP", true)
                        statusRow("Quét inventory và đường dẫn trùng", true)
                        statusRow("Giải mã tài nguyên AoV", false)
                        statusRow("Ghép AssetRef / CommonActions / Wwise", false)
                        statusRow("Xuất ZIP và kiểm tra CRC", false)
                    }.panel()

                    VStack(alignment: .leading, spacing: 10) {
                        Label("Cập nhật online", systemImage: "arrow.clockwise.circle").font(.headline)
                        Text(updateMessage).font(.footnote).foregroundColor(.secondary)
                        Button(action: checkUpdates) {
                            Label("Kiểm tra bản mới trên GitHub", systemImage: "arrow.triangle.2.circlepath")
                                .frame(maxWidth: .infinity)
                        }.buttonStyle(.bordered)
                        if let releaseURL {
                            Button("Mở trang phát hành") { openURL(releaseURL) }
                                .font(.footnote.weight(.semibold))
                        }
                        Text("Ứng dụng không tự thay thế IPA đã cài. Bản cập nhật cần được cài lại theo cơ chế iOS.")
                            .font(.caption2).foregroundColor(.secondary)
                    }.panel()
                }
                .padding(16)
            }
            .background(Color(uiColor: .systemGroupedBackground))
            .navigationTitle("AOV Skin Merger")
            .navigationBarTitleDisplayMode(.inline)
            .fileImporter(isPresented: $isImporting, allowedContentTypes: [.zip],
                          allowsMultipleSelection: true, onCompletion: importFiles)
            .alert("Không thể xử lý", isPresented: $showAlert) {
                Button("Đóng", role: .cancel) { showAlert = false }
            } message: { Text(alertText) }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "archivebox.fill").font(.system(size: 28)).foregroundColor(.accentColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text("AOV Skin Merger").font(.title2.bold())
                    Text("Bản iOS thử nghiệm 0.1.0").font(.caption).foregroundColor(.secondary)
                }
            }
            Text("Kiểm tra gói tài nguyên trực tiếp trên iPhone. Chưa có bộ ghép AoV hoàn chỉnh.")
                .font(.subheadline).foregroundColor(.secondary)
        }.panel()
    }

    private func analysisView(_ report: ArchiveAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Kết quả kiểm tra", systemImage: "checkmark.shield").font(.headline)
            metric("Số gói ZIP", "\(report.archives.count)")
            metric("Tổng số file", "\(report.archives.reduce(0) { $0 + $1.fileCount })")
            metric("Dung lượng ZIP", formatByteCount(report.totalInputBytes))
            metric("Dung lượng giải nén khai báo", formatByteCount(report.totalUncompressedBytes))
            metric("Đường dẫn trùng giữa các gói", "\(report.overlappingPaths.count)")
            ForEach(report.archives) { archive in
                VStack(alignment: .leading, spacing: 3) {
                    Text(archive.fileName).font(.subheadline.weight(.semibold)).lineLimit(1)
                    Text("\(archive.fileCount) file · \(formatByteCount(archive.uncompressedBytes)) sau giải nén")
                        .font(.caption).foregroundColor(.secondary)
                }
            }
            if !report.overlappingPaths.isEmpty {
                Text("Đường dẫn xuất hiện ở nhiều gói").font(.subheadline.weight(.semibold))
                ForEach(report.overlappingPaths.prefix(30)) { overlap in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(overlap.path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        Text(overlap.sourceNames.joined(separator: " · ")).font(.caption2).foregroundColor(.secondary)
                    }
                }
            }
            Text("Đường dẫn trùng chưa cho biết payload giống hay khác. Bản này chưa giải mã tài nguyên AoV và chưa tạo ZIP ghép.")
                .font(.footnote).foregroundColor(.orange)
        }.panel()
    }

    private func metric(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title).font(.subheadline).foregroundColor(.secondary)
            Spacer()
            Text(value).font(.subheadline.weight(.semibold)).monospacedDigit()
        }
    }

    private func statusRow(_ title: String, _ done: Bool) -> some View {
        HStack(spacing: 9) {
            Image(systemName: done ? "checkmark.circle.fill" : "circle")
                .foregroundColor(done ? .green : .secondary)
            Text(title).font(.subheadline)
            Spacer()
            if !done { Text("Chưa có").font(.caption2).foregroundColor(.secondary) }
        }
    }

    private func fileSize(_ url: URL) -> String {
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        return formatByteCount((attrs?[.size] as? NSNumber)?.uint64Value ?? 0)
    }

    private func importFiles(_ result: Result<[URL], Error>) {
        switch result {
        case .success(let urls):
            do {
                let folder = try sourceFolder()
                for source in urls {
                    let accessing = source.startAccessingSecurityScopedResource()
                    defer { if accessing { source.stopAccessingSecurityScopedResource() } }
                    let destination = folder.appendingPathComponent(UUID().uuidString + "-" + source.lastPathComponent)
                    try FileManager.default.copyItem(at: source, to: destination)
                    selectedURLs.append(destination)
                }
                analysis = nil
            } catch {
                alertText = error.localizedDescription
                showAlert = true
            }
        case .failure(let error):
            alertText = error.localizedDescription
            showAlert = true
        }
    }

    private func sourceFolder() throws -> URL {
        let docs = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask,
                                               appropriateFor: nil, create: true)
        let folder = docs.appendingPathComponent("SourceArchives", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }

    private func runAnalysis() {
        guard !selectedURLs.isEmpty else { return }
        isAnalyzing = true
        analysis = nil
        let urls = selectedURLs
        Task {
            do {
                analysis = try await Task.detached(priority: .userInitiated) {
                    try ArchiveScanner.analyze(urls)
                }.value
            } catch {
                alertText = error.localizedDescription
                showAlert = true
            }
            isAnalyzing = false
        }
    }

    private func checkUpdates() {
        updateMessage = "Đang kiểm tra GitHub…"
        releaseURL = nil
        Task {
            do {
                let release = try await UpdateService.latestRelease()
                releaseURL = release.html_url
                updateMessage = "Bản mới nhất: \(release.name ?? release.tag_name) (\(release.tag_name))."
            } catch {
                updateMessage = error.localizedDescription
            }
        }
    }
}

private extension View {
    func panel() -> some View {
        self.padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(uiColor: .secondarySystemGroupedBackground),
                        in: RoundedRectangle(cornerRadius: 18))
    }
}
