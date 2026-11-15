import SwiftUI
import UniformTypeIdentifiers
import UIKit

struct ContentView: View {
    @Environment(\.openURL) private var openURL
    @State private var isImporting = false
    @State private var isAnalyzing = false
    @State private var selectedURLs: [URL] = []
    @State private var analysis: ArchiveAnalysis?
    @State private var aesReport: AESScanReport?
    @State private var isDecoding = false
    @State private var isMerging = false
    @State private var mergeReport: AoVModMergeReport?
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
                                Label(isAnalyzing ? "Đang kiểm tra…" : "Kiểm tra cấu trúc và nội dung ZIP",
                                      systemImage: "doc.text.magnifyingglass")
                            }.frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .disabled(selectedURLs.isEmpty || isAnalyzing || isDecoding)
                        Button(action: runAESScan) {
                            HStack {
                                if isDecoding { ProgressView() }
                                Label(isDecoding ? "Đang quét AoV .bytes…" : "Quét giải mã AoV .bytes (AES/Zstd)",
                                      systemImage: "lock.open")
                            }.frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .disabled(selectedURLs.isEmpty || isAnalyzing || isDecoding)
                        Text("Quét tài nguyên và giải mã trực tiếp trên iPhone. Không upload ZIP.")
                            .font(.caption2).foregroundColor(.secondary)
                        Button(action: runModMerge) {
                            HStack {
                                if isMerging { ProgressView() }
                                Label(isMerging ? "Đang ghép MOD…" : "Ghép MOD và xuất ZIP",
                                      systemImage: "arrow.triangle.merge")
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(selectedURLs.count < 2 || isAnalyzing || isDecoding || isMerging)
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
                                        aesReport = nil
                                        mergeReport = nil
                                    } label: { Image(systemName: "xmark.circle.fill") }
                                    .buttonStyle(.plain)
                                }
                            }
                            Button("Xóa danh sách", role: .destructive) {
                                selectedURLs.removeAll()
                                analysis = nil
                                aesReport = nil
                                mergeReport = nil
                            }.font(.footnote)
                        }.panel()
                    }

                    if let analysis { analysisView(analysis) }
                    if let aesReport { aesReportView(aesReport) }
                    if let mergeReport { mergeResultView(mergeReport) }

                    VStack(alignment: .leading, spacing: 9) {
                        Label("Tiến độ phát triển", systemImage: "hammer.fill").font(.headline)
                        statusRow("Nhập nhiều ZIP và tự nhận gói nền", true)
                        statusRow("Quét inventory, CRC và SHA-256", true)
                        statusRow("Giải mã AES / AES+Zstandard / Zstandard AoV", true)
                        statusRow("AssetRef: nhận diện và giữ path duy nhất", true)
                        statusRow("CommonActions: direct list + skinId conditions", true)
                        statusRow("MSES / IFix / Wwise / AssetBundle metadata", false)
                        statusRow("Xuất ZIP với kiểm CRC khi không còn blocker", true)
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
            .navigationTitle("AKI AOV SKIN")
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
                    Text("AKI AOV SKIN").font(.title2.bold())
                    Text("Bản iOS thử nghiệm 0.4.0").font(.caption).foregroundColor(.secondary)
                }
            }
            Text("AKI AOV SKIN xử lý file ngay trên iPhone. Bộ ghép sẽ dừng khi gặp xung đột chưa có adapter đã xác minh.")
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
            metric("File đã đọc và kiểm CRC", "\(report.crcValidatedFiles)")
            metric("Đường dẫn trùng, payload giống", "\(report.identicalDuplicatePaths)")
            metric("Đường dẫn trùng, payload khác", "\(report.conflictingPaths)")
            ForEach(report.archives) { archive in
                VStack(alignment: .leading, spacing: 3) {
                    Text(archive.fileName).font(.subheadline.weight(.semibold)).lineLimit(1)
                    Text("\(archive.fileCount) file · \(formatByteCount(archive.uncompressedBytes)) sau giải nén")
                        .font(.caption).foregroundColor(.secondary)
                }
            }
            if !report.overlappingPaths.isEmpty {
                Text("Đường dẫn xuất hiện ở nhiều gói").font(.subheadline.weight(.semibold))
                ForEach(report.overlappingPaths.prefix(50)) { overlap in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 6) {
                            Image(systemName: overlap.identicalPayload ? "equal.circle.fill" : "exclamationmark.triangle.fill")
                                .foregroundColor(overlap.identicalPayload ? .green : .orange)
                            Text(overlap.identicalPayload ? "Payload giống hệt" : "Payload khác nhau")
                                .font(.caption.weight(.semibold))
                        }
                        Text(overlap.path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        ForEach(overlap.payloads) { payload in
                            Text("\(payload.archiveName) · SHA-256 \(String(payload.sha256.prefix(16)))…")
                                .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                        }
                    }
                    .padding(.vertical, 5)
                    Divider()
                }
            }
            Text("Quét đã đọc từng payload và xác minh CRC. Đường dẫn có payload khác nhau chỉ được đánh dấu xung đột, chưa tự chọn bản để ghi đè. Bản này chưa tạo ZIP skin hoàn chỉnh.")
                .font(.footnote).foregroundColor(.orange)
        }.panel()
    }

    private func aesReportView(_ report: AESScanReport) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Kết quả giải mã AoV AES / Zstandard", systemImage: "lock.open.fill").font(.headline)
            metric("AES thường giải mã được", "\(report.aesDecodedCount)")
            metric("AES+Zstandard giải mã được", "\(report.aesZstdDecodedCount)")
            metric("Zstandard độc lập giải mã được", "\(report.zstdDecodedCount)")
            metric("File .bytes khác / chưa nhận diện", "\(report.otherBytesFiles)")
            metric("Quá lớn để quét an toàn", "\(report.tooLargeToInspect)")
            metric("AssetReferenceSet nhận diện", "\(report.assetReferenceSets.count)")
            metric("AssetRef chưa nhận diện", "\(report.unrecognizedAssetRefCount)")
            if !report.assetReferenceSets.isEmpty {
                Text("AssetReferenceSet (kiểm kê đọc-only)").font(.subheadline.weight(.semibold))
                ForEach(report.assetReferenceSets) { item in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(item.path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        Text("Actor \(item.actorID ?? "không rõ") · \(item.wrapperMode) · \(formatByteCount(item.decodedBytes))")
                            .font(.caption2).foregroundColor(.secondary)
                        Text("baseSubset: có · skinSubset: \(item.hasSkinSubset ? "có" : "không thấy") · ref path: \(item.referencePathCandidates)")
                            .font(.caption2).foregroundColor(.secondary)
                        ForEach(item.sampleReferences, id: \.self) { reference in
                            Text(reference).font(.caption2.monospaced()).textSelection(.enabled)
                        }
                    }
                    .padding(.vertical, 5)
                    Divider()
                }
            }
            if !report.decoded.isEmpty {
                Text("Payload đã giải mã (chỉ đọc)").font(.subheadline.weight(.semibold))
                ForEach(report.decoded) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        Text("\(item.wrapperMode) · \(formatByteCount(item.decodedBytes)) · SHA-256 \(String(item.sha256.prefix(20)))…")
                            .font(.caption2).foregroundColor(.secondary).textSelection(.enabled)
                    }
                }
            }
            if !report.invalidAES.isEmpty {
                Text("Không giải mã được").font(.subheadline.weight(.semibold))
                ForEach(Array(report.invalidAES.enumerated()), id: \.offset) { _, item in
                    Text(item).font(.caption2).foregroundColor(.orange).textSelection(.enabled)
                }
            }
            Text("Giải mã này chỉ tạo kết quả kiểm tra trong bộ nhớ, không sửa ZIP nguồn, không sửa AssetRef/MSES và chưa tạo skin mod.")
                .font(.footnote).foregroundColor(.orange)
        }
        .panel()
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
                aesReport = nil
                mergeReport = nil
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

    private func mergeResultView(_ report: AoVModMergeReport) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("ZIP đã ghép và kiểm tra cấu trúc", systemImage: "checkmark.seal.fill")
                .font(.headline)
                .foregroundColor(.green)
            Text(report.outputURL.lastPathComponent)
                .font(.subheadline.weight(.semibold))
                .textSelection(.enabled)
            metric("Gói nguồn", "\(report.sourceCount)")
            metric("Gói nền được chọn", report.baseSource)
            metric("Tài nguyên mới", "\(report.addedResources)")
            metric("Tài nguyên thay thế", "\(report.replacedResources)")
            metric("CommonActions đã ghép", "\(report.commonActionsFiles.count)")
            metric("Bảng MSES đã ghép", "\(report.mergedMSESTables.count)")
            metric("IFix patch đã ghép", "\(report.mergedIFixPatches.count)")
            metric("AssetRef duy nhất giữ nguyên", "\(report.preservedAssetReferenceSets.count)")
            ShareLink(item: report.outputURL) {
                Label("Chia sẻ / lưu ZIP kết quả", systemImage: "square.and.arrow.up")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            Text("ZIP đã qua kiểm CRC và kiểm tra cấu trúc. Chưa thử trong Liên Quân; đây không phải xác nhận tương thích game. Nếu một loại xung đột chưa có adapter, app sẽ dừng và không xuất ZIP.")
                .font(.footnote)
                .foregroundColor(.orange)
        }
        .panel()
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

    private func runModMerge() {
        guard selectedURLs.count >= 2 else { return }
        isMerging = true
        mergeReport = nil
        let urls = selectedURLs
        Task {
            do {
                let report = try await Task.detached(priority: .userInitiated) {
                    try AoVModArchiveMerger.merge(urls)
                }.value
                mergeReport = report
            } catch {
                alertText = error.localizedDescription
                showAlert = true
            }
            isMerging = false
        }
    }

    private func runAESScan() {
        guard !selectedURLs.isEmpty else { return }
        isDecoding = true
        aesReport = nil
        let urls = selectedURLs
        Task {
            do {
                aesReport = try await Task.detached(priority: .userInitiated) {
                    try ArchiveScanner.inspectAESResources(urls)
                }.value
            } catch {
                alertText = error.localizedDescription
                showAlert = true
            }
            isDecoding = false
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
