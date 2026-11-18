import SwiftUI
import UniformTypeIdentifiers

private enum ZIPImportPurpose: Equatable {
    case merger
    case remakeBase
    case legacyMods
}

struct ContentView: View {
    @Environment(\.openURL) private var openURL
    @State private var isImporting = false
    @State private var importPurpose: ZIPImportPurpose = .merger
    @State private var isCopyingFiles = false
    @State private var remakeBaseURL: URL?
    @State private var remakeMODURLs: [URL] = []
    @State private var isAnalyzingRemake = false
    @State private var remakeReport: AoVModRemakeReport?
    @State private var isExportingRemake = false
    @State private var remakeExportReport: AoVModRemakeExportReport?
    @State private var isAnalyzing = false
    @State private var selectedURLs: [URL] = []
    @State private var selectedBaseURL: URL?
    @State private var showBasePicker = false
    @State private var analysis: ArchiveAnalysis?
    @State private var aesReport: AESScanReport?
    @State private var isDecoding = false
    @State private var isMerging = false
    @State private var mergeReport: AoVModMergeReport?
    @State private var alertText = ""
    @State private var showAlert = false
    @State private var updateMessage = "Chưa kiểm tra cập nhật"
    @State private var releaseURL: URL?
    @State private var selectedTab = "Trang chủ"

    var body: some View {
        NavigationStack {
            ZStack {
                ZStack {
                    LinearGradient(
                        colors: [Color(red: 0.015, green: 0.012, blue: 0.045),
                                 Color(red: 0.075, green: 0.045, blue: 0.15),
                                 Color(red: 0.012, green: 0.012, blue: 0.035)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    )
                    Circle().fill(Color.purple.opacity(0.18)).frame(width: 260).blur(radius: 100)
                        .offset(x: 150, y: -300)
                    Circle().fill(Color(red: 0.45, green: 0.30, blue: 1).opacity(0.12))
                        .frame(width: 230).blur(radius: 85).offset(x: -160, y: 130)
                }
                .ignoresSafeArea()

                Group {
                    if selectedTab == "Tính năng" {
                        ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 18) {
                        header
                        quickStatus
                        VStack(alignment: .leading, spacing: 12) {
                            Label("Nguồn MOD", systemImage: "doc.zipper").font(.headline)
                            Text("Chọn nhiều ZIP. File được giữ trên iPhone, không tải lên máy chủ.")
                                .font(.footnote).foregroundColor(.secondary)
                            Button { importPurpose = .merger; isImporting = true } label: {
                                HStack {
                                    if isCopyingFiles { ProgressView() }
                                    Label(
                                        isCopyingFiles ? "Đang nhập ZIP vào iPhone…" : "Chọn file ZIP",
                                        systemImage: isCopyingFiles ? "arrow.down.doc" : "plus.circle.fill"
                                    )
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(AKIPrimaryButtonStyle())
                            .disabled(isCopyingFiles || isAnalyzing || isDecoding || isMerging)
                            Button(action: runAnalysis) {
                                HStack {
                                    if isAnalyzing { ProgressView().tint(.white) }
                                    Label(isAnalyzing ? "Đang kiểm tra…" : "Kiểm tra cấu trúc và nội dung ZIP",
                                          systemImage: "doc.text.magnifyingglass")
                                }.frame(maxWidth: .infinity)
                            }
                            .buttonStyle(AKISecondaryButtonStyle())
                            .disabled(selectedURLs.isEmpty || isAnalyzing || isDecoding || isCopyingFiles || isMerging)
                            Button(action: runAESScan) {
                                HStack {
                                    if isDecoding { ProgressView() }
                                    Label(isDecoding ? "Đang quét AoV .bytes…" : "Quét giải mã AoV .bytes (AES/Zstd)",
                                          systemImage: "lock.open")
                                }.frame(maxWidth: .infinity)
                            }
                            .buttonStyle(AKISecondaryButtonStyle())
                            .disabled(selectedURLs.isEmpty || isAnalyzing || isDecoding || isCopyingFiles || isMerging)
                            Text("Có thể chọn ZIP tại đây, hoặc mở ZIP từ Tệp bằng Chia sẻ → Mở bằng AKI AOV SKIN. Thư mục của app cũng xuất hiện ở Trên iPhone của tôi → AKI AOV SKIN; ZIP đã nhập nằm trong SourceArchives.")
                                .font(.caption2).foregroundColor(.secondary)
                            Button(action: runModMerge) {
                                HStack {
                                    if isMerging { ProgressView() }
                                    Label(isMerging ? "Đang ghép MOD…" : "Ghép MOD và xuất ZIP",
                                          systemImage: "arrow.triangle.merge")
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(AKIPrimaryButtonStyle())
                            .disabled(selectedURLs.count < 2 || isAnalyzing || isDecoding || isMerging || isCopyingFiles)
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
                                            let removedURL = selectedURLs.remove(at: index)
                                            if selectedBaseURL == removedURL { selectedBaseURL = nil }
                                            analysis = nil
                                            aesReport = nil
                                            mergeReport = nil
                                        } label: { Image(systemName: "xmark.circle.fill") }
                                        .buttonStyle(.plain)
                                    }
                                }
                                Button("Xóa danh sách", role: .destructive) {
                                    selectedURLs.removeAll()
                                    selectedBaseURL = nil
                                    analysis = nil
                                    aesReport = nil
                                    mergeReport = nil
                                }.font(.footnote)
                            }.panel()
                        }
    
                        if let analysis { analysisView(analysis) }
                        if let aesReport { aesReportView(aesReport) }
                        if let mergeReport { mergeResultView(mergeReport) }
                        remakePanel
                        if let remakeReport { remakeResultView(remakeReport) }
                        if let remakeExportReport { remakeExportResultView(remakeExportReport) }
    
                        VStack(alignment: .leading, spacing: 9) {
                            Label("Tiến độ phát triển", systemImage: "hammer.fill").font(.headline)
                            statusRow("Nhập nhiều ZIP và tự nhận gói nền", true)
                            statusRow("Quét inventory, CRC và SHA-256", true)
                            statusRow("Giải mã AES / AES+Zstandard / Zstandard AoV", true)
                            statusRow("AssetRef: nhận diện và giữ path duy nhất", true)
                            statusRow("CommonActions: direct list + skinId conditions", true)
                            statusRow("MSES / IFix: adapter theo schema đã nhận diện", true)
                            statusRow("Wwise BNK: append-only nếu ID không xung đột", true)
                            statusRow("UnityFS read-only: none / LZMA1 / LZ4 / LZ4HC + AoV encryption", true)
                            statusRow("Packer / Verification: writer + UnityFS round-trip đã có; chưa nối RES end-to-end", false)
                            statusRow("AssetRef subset merge khi cùng path khác payload", false)
                            statusRow("Xuất ZIP với kiểm CRC khi không còn blocker", true)
                        }.panel()
    
                        VStack(alignment: .leading, spacing: 10) {
                            Label("Cập nhật online", systemImage: "arrow.clockwise.circle").font(.headline)
                            Text(updateMessage).font(.footnote).foregroundColor(.secondary)
                            Button(action: checkUpdates) {
                                Label("Kiểm tra bản mới trên GitHub", systemImage: "arrow.triangle.2.circlepath")
                                    .frame(maxWidth: .infinity)
                            }.buttonStyle(AKISecondaryButtonStyle())
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
                    } else if selectedTab == "Trang chủ" {
                        homeDashboard
                    } else if selectedTab == "Lịch sử" {
                        historyDashboard
                    } else {
                        infoDashboard
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
            .safeAreaInset(edge: .bottom, spacing: 0) { bottomNavigation }
            .toolbar(.hidden, for: .navigationBar)
            .tint(.white)
            .preferredColorScheme(.dark)
            .onOpenURL { url in
                importFiles(.success([url]))
            }
            .fileImporter(
                isPresented: $isImporting,
                allowedContentTypes: [UTType(filenameExtension: "zip") ?? .zip],
                allowsMultipleSelection: true
            ) { result in
                importFiles(result, purpose: importPurpose)
            }
            .task {
                do {
                    _ = try sourceFolder()
                } catch {
                    alertText = "Không thể tạo thư mục lưu ZIP: \(error.localizedDescription)"
                    showAlert = true
                }
            }
            .alert("Thông báo", isPresented: $showAlert) {
                Button("Đóng", role: .cancel) { showAlert = false }
            } message: { Text(alertText) }
            .confirmationDialog("Chọn ZIP làm gói nền", isPresented: $showBasePicker, titleVisibility: .visible) {
                ForEach(Array(selectedURLs.enumerated()), id: \.offset) { item in
                    Button(item.element.lastPathComponent) { selectedBaseURL = item.element }
                }
                Button("Hủy", role: .cancel) { }
            } message: {
                Text("Gói được chọn sẽ làm nền; các ZIP còn lại được ghép vào gói này.")
            }
        }
    }


    private var remakePanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("RMK + RES phiên bản mới", systemImage: "arrow.triangle.2.circlepath")
                .font(.headline)
            Text("Chế độ riêng để phân tích MOD cũ theo bộ RES mới. Không thay đổi danh sách ghép skin và không ghi đè tài nguyên.")
                .font(.footnote).foregroundColor(.secondary)

            Button {
                importPurpose = .remakeBase
                isImporting = true
            } label: {
                Label(remakeBaseURL.map { "RES mới: \($0.lastPathComponent)" } ?? "Chọn RES mới (1 ZIP)", systemImage: "square.stack.3d.up.fill")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(AKISecondaryButtonStyle())
            .disabled(isCopyingFiles || isAnalyzingRemake)

            Button {
                importPurpose = .legacyMods
                isImporting = true
            } label: {
                Label("Thêm MOD cũ (tối đa 10 ZIP)", systemImage: "doc.zipper")
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(AKISecondaryButtonStyle())
            .disabled(isCopyingFiles || isAnalyzingRemake)

            if !remakeMODURLs.isEmpty {
                HStack {
                    Text("MOD cũ đã chọn").font(.subheadline.weight(.semibold))
                    Spacer()
                    Text("\(remakeMODURLs.count) ZIP").font(.caption).foregroundColor(.secondary)
                }
                ForEach(Array(remakeMODURLs.enumerated()), id: \.offset) { index, url in
                    HStack {
                        Image(systemName: "archivebox").foregroundColor(.accentColor)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(url.lastPathComponent).font(.caption).lineLimit(1)
                            Text(fileSize(url)).font(.caption2).foregroundColor(.secondary)
                        }
                        Spacer()
                        Button(role: .destructive) {
                            remakeMODURLs.remove(at: index)
                            remakeReport = nil
                            remakeExportReport = nil
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            Button(action: runRemakeAnalysis) {
                HStack {
                    if isAnalyzingRemake { ProgressView().tint(.white) }
                    Label(isAnalyzingRemake ? "Đang đối chiếu RES và MOD…" : "Phân tích RMK / RES", systemImage: "arrow.triangle.2.circlepath")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(AKIPrimaryButtonStyle())
            .disabled(remakeBaseURL == nil || remakeMODURLs.isEmpty || isAnalyzingRemake || isCopyingFiles || isMerging)

            Text("Đã có parser, planner và writer riêng cho catalog Packer/Verification, cùng bộ ghi AssetBundle class 142 trong UnityFS. Chưa xuất ZIP remake cho đến khi payload thật, dependency và checksum của RES được kiểm chứng xuyên suốt.")
                .font(.caption2).foregroundColor(.orange)
        }
        .panel()
    }

    private func remakeResultView(_ report: AoVModRemakeReport) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Báo cáo đối chiếu RMK + RES", systemImage: "doc.text.magnifyingglass")
                .font(.headline)
            metric("RES nền", report.base.fileName)
            metric("Version RES", report.baseVersion ?? "Không tìm thấy version.txt")
            metric("MOD cũ", "\(report.legacy.count)")
            VStack(alignment: .leading, spacing: 10) {
                Text("Nhận diện họ tài nguyên")
                    .font(.subheadline.weight(.bold))
                Text("Phân loại tự động theo path và metadata của từng gói. Nhãn adapter cho biết mức xử lý hiện có, không đồng nghĩa mọi payload đều đã ghép được.")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                ForEach(report.packageAudits) { package in
                    VStack(alignment: .leading, spacing: 7) {
                        Text(package.archiveName)
                            .font(.caption.weight(.semibold))
                            .lineLimit(1)
                        metric("Build · file · giải nén",
                               "\(package.version ?? "Không rõ") · \(package.fileCount) · \(formatByteCount(package.expandedBytes))")
                        ForEach(Array(package.families.prefix(8))) { family in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack(alignment: .firstTextBaseline) {
                                    Text(family.family.title)
                                        .font(.caption.weight(.semibold))
                                    Spacer()
                                    Text("\(family.fileCount) file · \(formatByteCount(family.totalBytes))")
                                        .font(.caption2.monospacedDigit())
                                        .foregroundColor(.secondary)
                                }
                                if family.parsedUnityFSCount > 0 {
                                    Text("UnityFS đã phân tích: \(family.parsedUnityFSCount)")
                                        .font(.caption2)
                                        .foregroundColor(.green)
                                }
                                Text(family.family.supportSummary)
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                                ForEach(family.samples, id: \.self) { sample in
                                    Text(sample)
                                        .font(.system(size: 9, design: .monospaced))
                                        .foregroundColor(.secondary)
                                        .textSelection(.enabled)
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    }
                    .padding(10)
                    .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(.vertical, 6)
            Divider().overlay(.white.opacity(0.12))
            metric("Path giống payload", "\(report.identicalCount)")
            metric("Path cùng tên, payload khác", "\(report.changedCount)")
            metric("Path chỉ có ở MOD cũ", "\(report.legacyOnlyCount)")
            metric("Path chỉ có ở RES mới", "\(report.baseOnlyPathCount)")
            metric("Packer Catalog RES", report.baseHasPackerCatalog ? "Có" : "Thiếu")
            metric("Verification Catalog RES", report.baseHasVerificationCatalog ? "Có" : "Thiếu")

            Divider().overlay(.white.opacity(0.12))
            catalogComparisonView("Packer AssetBundle wrappers", report.packerCatalogComparison)
            catalogComparisonView("Verification AssetBundle wrappers", report.verificationCatalogComparison)

            Text("Runtime catalog thật").font(.subheadline.weight(.bold))
            runtimeCatalogSectionView(report.runtimeCatalogComparison.packerResources)
            runtimeCatalogSectionView(report.runtimeCatalogComparison.packerParentChildLinks)
            runtimeCatalogSectionView(report.runtimeCatalogComparison.verificationZIP)
            runtimeCatalogSectionView(report.runtimeCatalogComparison.verificationDatabin)

            Text("Kế hoạch ghi catalog").font(.subheadline.weight(.bold))
            runtimeCatalogWritePlanView(
                title: "Packer",
                planExists: report.runtimePackerMergePlan != nil,
                safeToWrite: report.runtimePackerMergePlan?.safeToWrite ?? false,
                details: report.runtimePackerMergePlan.map { plan in
                    [
                        "Mục Packer mới đã có bundle kiểm chứng: \(plan.addedPaths.count)",
                        "Bundle hiện có được thay sau khi ghép kiểm chứng: \(plan.updatedPaths.count)",
                        "Bundle cùng path còn bị chặn: \(plan.changedExistingPaths.count)",
                        "Parent-child link mới: \(plan.addedParentChildLinkCount)"
                    ]
                } ?? [],
                issues: report.runtimePackerMergePlan?.issues ?? []
            )
            runtimeCatalogWritePlanView(
                title: "Verification",
                planExists: report.runtimeVerificationMergePlan != nil,
                safeToWrite: report.runtimeVerificationMergePlan?.safeToWrite ?? false,
                details: report.runtimeVerificationMergePlan.map { plan in
                    [
                        "ZIP entry mới: \(plan.addedZIPPaths.count)",
                        "ZIP checksum đã cập nhật: \(plan.updatedZIPPaths.count)",
                        "Databin cần cập nhật CRC: \(plan.updatedDatabinPaths.count)",
                        "Databin mới: \(plan.addedDatabinPaths.count)"
                    ]
                } ?? [],
                issues: report.runtimeVerificationMergePlan?.issues ?? []
            )

            catalogMergePlanView(report.catalogMergePlanFindings)
            cabMergeCandidateView(report.cabMergeCandidates)

            if !report.legacyVersions.isEmpty {
                Divider().overlay(.white.opacity(0.12))
                Text("Phiên bản MOD nguồn").font(.subheadline.weight(.semibold))
                ForEach(report.legacyVersions.keys.sorted(), id: \.self) { name in
                    metric(name, report.legacyVersions[name] ?? "Không rõ")
                }
            }
            if !report.changedPaths.isEmpty {
                Divider().overlay(.white.opacity(0.12))
                Text("Payload khác cần remap").font(.subheadline.weight(.semibold))
                ForEach(Array(report.changedPaths.prefix(12)), id: \.id) { finding in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(finding.path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                        Text("Nguồn: \(finding.legacySources.joined(separator: ", "))")
                            .font(.caption2).foregroundColor(.secondary)
                    }
                }
            }
            if !report.legacyOnlyPaths.isEmpty {
                Divider().overlay(.white.opacity(0.12))
                Text("Tài nguyên chưa có path tương ứng trong RES").font(.subheadline.weight(.semibold))
                ForEach(Array(report.legacyOnlyPaths.prefix(12)), id: \.id) { finding in
                    Text(finding.path).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                }
            }
            Divider().overlay(.white.opacity(0.12))
            ForEach(Array(report.warnings.enumerated()), id: \.offset) { _, warning in
                Label(warning, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundColor(.orange)
            }
            Text("CAB đã được quét và đối chiếu class 142 theo bundle identity. Bước tự động nhập object closure và cập nhật checksum xuyên toàn bộ RES vẫn chưa được bật.")
                .font(.caption).foregroundColor(.orange)
            Button(action: runRemakeSafeExport) {
                HStack {
                    if isExportingRemake { ProgressView().tint(.white) }
                    Label(
                        isExportingRemake ? "Đang tạo RES an toàn…" : "Xuất phần tài nguyên độc lập an toàn",
                        systemImage: "shippingbox.and.arrow.backward"
                    )
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(AKIPrimaryButtonStyle())
            .disabled(remakeBaseURL == nil || remakeMODURLs.isEmpty || remakeReport == nil ||
                      isAnalyzingRemake || isExportingRemake || isCopyingFiles || isMerging)
            Text("Payload được giữ nguyên byte và dùng metadata từ MOD cùng build. Chưa xác nhận tương thích trong game.")
                .font(.footnote.weight(.semibold)).foregroundColor(.orange)
        }
        .panel()
    }

    private func runtimeCatalogSectionView(_ report: AoVRuntimeCatalogSectionReport) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(report.title).font(.caption.weight(.bold))
            if !report.currentCatalogVersions.isEmpty {
                Text("RES: " + report.currentCatalogVersions.joined(separator: ", "))
                    .font(.caption2).foregroundColor(.secondary)
            } else {
                Text("RES: chưa đọc được catalog runtime")
                    .font(.caption2).foregroundColor(.orange)
            }
            if !report.legacyCatalogVersions.isEmpty {
                Text("MOD: " + report.legacyCatalogVersions.joined(separator: " · "))
                    .font(.caption2).foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            metric("Số mục RES / MOD", "\(report.currentEntryCount) / \(report.legacyEntryCount)")
            metric("Nội dung trùng", "\(report.identicalEntryCount)")
            metric("Cùng path, khác dữ liệu", "\(report.changedEntryCount)")
            metric("Chỉ có trong MOD", "\(report.legacyOnlyEntryCount)")
            metric("Chỉ có trong RES", "\(report.currentOnlyEntryCount)")
            metric("Đường dẫn trùng / xung đột", "\(report.duplicateCurrentPathCount + report.duplicateLegacyPathCount + report.conflictingLegacyPathCount)")
            metric("Tham chiếu không hợp lệ", "\(report.invalidReferenceCount)")

            ForEach(Array(report.findings.prefix(6))) { finding in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(alignment: .top, spacing: 8) {
                        Text(runtimeCatalogDifferenceText(finding.difference))
                            .font(.caption2.weight(.bold))
                            .foregroundColor(runtimeCatalogDifferenceColor(finding.difference))
                        Spacer(minLength: 3)
                    }
                    Text(finding.path)
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundColor(.white.opacity(0.92))
                        .textSelection(.enabled)
                    if let currentDetail = finding.currentDetail {
                        Text("RES: " + currentDetail)
                            .font(.caption2).foregroundColor(.secondary)
                    }
                    ForEach(Array(finding.legacyDetails.enumerated()), id: \.offset) { _, detail in
                        Text("MOD: " + detail)
                            .font(.caption2).foregroundColor(.orange.opacity(0.9))
                    }
                }
                .padding(.top, 4)
            }
            if report.findings.count > 6 {
                Text("Còn \(report.findings.count - 6) khác biệt hiển thị trong báo cáo dữ liệu.")
                    .font(.caption2).foregroundColor(.secondary)
            }
        }
        .padding(11)
        .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
    }

    private func runtimeCatalogWritePlanView(
        title: String,
        planExists: Bool,
        safeToWrite: Bool,
        details: [String],
        issues: [AoVRuntimeCatalogMergeIssue]
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(title).font(.caption.weight(.bold))
                Spacer()
                Text(!planExists ? "CHƯA ĐỦ DỮ LIỆU" : (safeToWrite ? "ĐÃ QUA KIỂM TRA CATALOG" : "ĐANG BỊ CHẶN"))
                    .font(.caption2.weight(.bold))
                    .foregroundColor(!planExists ? .secondary : (safeToWrite ? .green : .orange))
            }
            if planExists {
                ForEach(Array(details.enumerated()), id: \.offset) { _, detail in
                    Text(detail).font(.caption2).foregroundColor(.secondary)
                }
                ForEach(Array(issues.prefix(6))) { issue in
                    Text("\(issue.path.isEmpty ? "(đường dẫn rỗng)" : issue.path) · \(issue.reason.rawValue)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(.orange)
                        .textSelection(.enabled)
                }
                if issues.count > 6 {
                    Text("Còn \(issues.count - 6) vấn đề khác.")
                        .font(.caption2).foregroundColor(.secondary)
                }
            } else {
                Text("Cần đọc được đúng một catalog từ RES nền trước khi lập kế hoạch ghi.")
                    .font(.caption2).foregroundColor(.secondary)
            }
        }
        .padding(11)
        .background(.white.opacity(0.035), in: RoundedRectangle(cornerRadius: 13, style: .continuous))
    }

    private func runtimeCatalogDifferenceText(_ difference: AoVRuntimeCatalogDifference) -> String {
        switch difference {
        case .identical: return "TRÙNG"
        case .changed: return "ĐỔI DỮ LIỆU"
        case .legacyOnly: return "CHỈ CÓ MOD"
        case .currentOnly: return "CHỈ CÓ RES"
        case .duplicateCurrent: return "RES TRÙNG PATH"
        case .conflictingLegacy: return "MOD XUNG ĐỘT"
        }
    }

    private func runtimeCatalogDifferenceColor(_ difference: AoVRuntimeCatalogDifference) -> Color {
        switch difference {
        case .identical: return .green
        case .changed, .legacyOnly, .conflictingLegacy, .duplicateCurrent: return .orange
        case .currentOnly: return .secondary
        }
    }

    private func catalogComparisonView(
        _ title: String,
        _ report: AoVRemakeCatalogKindReport
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.subheadline.weight(.semibold))
            metric("AssetBundle records RES mới", "\(report.currentRecordCount)")
            metric("AssetBundle records MOD cũ", "\(report.legacyRecordCount)")
            metric("Ánh xạ bundle duy nhất", "\(report.mappedRecordCount)")
            metric("Bundle cũ không còn trong RES", "\(report.missingCurrentRecordCount)")
            metric("Bundle trùng tên, chưa phân biệt được", "\(report.ambiguousRecordCount)")
            metric("Container key khớp", "\(report.mappedContainerKeyCount)")
            metric("Key chỉ có ở MOD cũ", "\(report.legacyOnlyContainerKeyCount)")
            metric("Key chỉ có ở RES mới", "\(report.currentOnlyContainerKeyCount)")
            metric("Key trùng không an toàn", "\(report.ambiguousContainerKeyCount)")
            metric("Asset pointer thay đổi", "\(report.changedAssetPointerCount)")
            metric("Preload range thay đổi", "\(report.changedPreloadRangeCount)")
            metric("Preload range sai trong MOD cũ", "\(report.invalidLegacyPreloadRangeCount)")
            metric("Preload range sai trong RES mới", "\(report.invalidCurrentPreloadRangeCount)")
            metric("Main asset pointer thay đổi", "\(report.changedMainAssetPointerCount)")
            metric("Class compatibility thay đổi", "\(report.changedClassCompatibilityCount)")
            metric("Pointer nội bộ MOD không tìm thấy", "\(report.unresolvedLegacyLocalPointerCount)")
            metric("Pointer nội bộ RES không tìm thấy", "\(report.unresolvedCurrentLocalPointerCount)")
            metric("External fileID sai trong MOD", "\(report.invalidLegacyExternalPointerCount)")
            metric("External fileID sai trong RES", "\(report.invalidCurrentExternalPointerCount)")
            metric("Dependency mới ở RES", "\(report.dependenciesAddedInCurrentCount)")
            metric("Dependency cũ không còn", "\(report.dependenciesRemovedInCurrentCount)")

            ForEach(Array(report.findings.prefix(8).enumerated()), id: \.offset) { _, finding in
                VStack(alignment: .leading, spacing: 3) {
                    HStack {
                        Text(finding.assetBundleName)
                            .font(.caption.weight(.semibold))
                            .lineLimit(1)
                        Spacer()
                        Text(catalogStatusText(finding.status))
                            .font(.caption2)
                            .foregroundColor(finding.status == .mappedCandidate ? .green : .orange)
                    }
                    Text("MOD: \(finding.sourceArchive) · key khớp \(finding.mappedContainerKeyCount) · pointer đổi \(finding.changedAssetPointerCount)")
                        .font(.caption2).foregroundColor(.secondary)
                    if finding.invalidLegacyPreloadRangeCount > 0 || finding.invalidCurrentPreloadRangeCount > 0 {
                        Text("Preload range lỗi: MOD \(finding.invalidLegacyPreloadRangeCount) · RES \(finding.invalidCurrentPreloadRangeCount)")
                            .font(.caption2).foregroundColor(.orange)
                    }
                    if finding.mainAssetPointerChanged || finding.classCompatibilityChanged {
                        Text("Metadata đổi: main asset \(finding.mainAssetPointerChanged ? "có" : "không") · class compatibility \(finding.classCompatibilityChanged ? "có" : "không")")
                            .font(.caption2).foregroundColor(.orange)
                    }
                    if !finding.legacyOnlyKeySamples.isEmpty {
                        Text("MOD-only: " + finding.legacyOnlyKeySamples.joined(separator: ", "))
                            .font(.caption2.monospaced()).foregroundColor(.orange)
                    }
                    if !finding.currentOnlyKeySamples.isEmpty {
                        Text("RES-only: " + finding.currentOnlyKeySamples.joined(separator: ", "))
                            .font(.caption2.monospaced()).foregroundColor(.secondary)
                    }
                    if !finding.changedPointerKeySamples.isEmpty {
                        Text("Pointer cần remap: " + finding.changedPointerKeySamples.joined(separator: ", "))
                            .font(.caption2.monospaced()).foregroundColor(.orange)
                    }
                    if !finding.dependenciesAddedInCurrent.isEmpty {
                        Text("Dependency mới: " + finding.dependenciesAddedInCurrent.joined(separator: ", "))
                            .font(.caption2).foregroundColor(.secondary)
                    }
                    if !finding.legacyPointerAuditSamples.isEmpty || !finding.currentPointerAuditSamples.isEmpty {
                        ForEach(Array(finding.legacyPointerAuditSamples.prefix(3).enumerated()), id: \.offset) { _, sample in
                            Text("MOD pointer: " + sample).font(.caption2).foregroundColor(.orange)
                        }
                        ForEach(Array(finding.currentPointerAuditSamples.prefix(3).enumerated()), id: \.offset) { _, sample in
                            Text("RES pointer: " + sample).font(.caption2).foregroundColor(.orange)
                        }
                    }
                }
                .padding(.vertical, 4)
                Divider().overlay(.white.opacity(0.08))
            }
            if report.findings.count > 8 {
                Text("Hiển thị 8 / \(report.findings.count) bản ghi cần xem xét.")
                    .font(.caption2).foregroundColor(.secondary)
            }
            Text("Các ánh xạ này là ứng viên phân tích, không phải lệnh ghi pointer cũ sang catalog mới.")
                .font(.caption2).foregroundColor(.orange)
        }
    }

    private func catalogMergePlanView(_ findings: [AoVRemakeCatalogPlanFinding]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Kế hoạch ghép catalog thử")
                .font(.subheadline.weight(.semibold))
            metric("Bundle có ứng viên ghép", "\(findings.filter { $0.status == .candidate || $0.status == .requiresExternalTargetAudit }.count)")
            metric("Bundle bị chặn / không khớp", "\(findings.filter { $0.status == .blocked || $0.status == .missingCurrentBundle || $0.status == .ambiguousCurrentBundle }.count)")
            metric("Bundle cần audit external CAB", "\(findings.filter { $0.status == .requiresExternalTargetAudit }.count)")
            metric("Bundle không có thay đổi", "\(findings.filter { $0.status == .noChanges }.count)")

            ForEach(Array(findings.prefix(16)), id: \.id) { finding in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(finding.bundleName)
                            .font(.caption.weight(.semibold))
                            .lineLimit(1)
                        Spacer()
                        Text(catalogPlanStatusText(finding.status))
                            .font(.caption2)
                            .foregroundColor(catalogPlanStatusColor(finding.status))
                    }
                    Text("\(finding.kind.displayName) · nguồn: \(finding.sourceArchives.joined(separator: ", "))")
                        .font(.caption2).foregroundColor(.secondary)
                    if !finding.addedContainerKeys.isEmpty {
                        Text("Sẽ thêm: " + finding.addedContainerKeys.prefix(6).joined(separator: ", "))
                            .font(.caption2.monospaced()).foregroundColor(.green)
                    }
                    if !finding.alreadyPresentContainerKeys.isEmpty {
                        Text("Giữ theo RES: " + finding.alreadyPresentContainerKeys.prefix(6).joined(separator: ", "))
                            .font(.caption2.monospaced()).foregroundColor(.secondary)
                    }
                    if !finding.currentVersionRetainedPointerConflictKeys.isEmpty {
                        Text("Key trùng nhưng pointer MOD khác, vẫn giữ RES: " + finding.currentVersionRetainedPointerConflictKeys.prefix(6).joined(separator: ", "))
                            .font(.caption2.monospaced()).foregroundColor(.orange)
                    }
                    if !finding.addedDependencies.isEmpty {
                        Text("Dependency cần thêm: " + finding.addedDependencies.joined(separator: ", "))
                            .font(.caption2).foregroundColor(.secondary)
                    }
                    if finding.remappedExternalPointerCount > 0 {
                        Text("External pointer đã đổi fileID: \(finding.remappedExternalPointerCount), cần kiểm tra object đích trong CAB được tham chiếu.")
                            .font(.caption2).foregroundColor(.orange)
                    }
                    if !finding.externalTargetAuditSamples.isEmpty {
                        Text("Cần audit: " + finding.externalTargetAuditSamples.prefix(4).joined(separator: ", "))
                            .font(.caption2.monospaced()).foregroundColor(.orange)
                    }
                    if !finding.reasons.isEmpty {
                        ForEach(Array(finding.reasons.prefix(3).enumerated()), id: \.offset) { _, reason in
                            Text(reason).font(.caption2).foregroundColor(.orange)
                        }
                    }
                    if let nodePath = finding.currentNodePath {
                        Text("RES node: \(nodePath) · object pathID \(finding.currentObjectPathID ?? 0)")
                            .font(.caption2).foregroundColor(.secondary)
                    }
                }
                .padding(.vertical, 4)
                Divider().overlay(.white.opacity(0.08))
            }
            if findings.count > 16 {
                Text("Hiển thị 16 / \(findings.count) kế hoạch.")
                    .font(.caption2).foregroundColor(.secondary)
            }
            Text("Đây là kế hoạch merge candidate, chưa ghi vào Packer/Verification và chưa xuất ZIP remake.")
                .font(.caption2.weight(.semibold)).foregroundColor(.orange)
        }
    }

    private func cabMergeCandidateView(_ findings: [AoVRemakeCABCandidateFinding]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Đối chiếu CAB / AssetBundle class 142")
                .font(.subheadline.weight(.semibold))
            metric("Bản ghi CAB đọc được", "\(findings.count)")
            metric("Khớp duy nhất, đủ điều kiện xem tiếp", "\(findings.filter { $0.status == .candidate }.count)")
            metric("Thiếu / trùng / không tương thích / key trùng",
                   "\(findings.filter { $0.status != .candidate }.count)")
            ForEach(Array(findings.prefix(14))) { finding in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(finding.bundleName.isEmpty ? "(bundle không tên)" : finding.bundleName)
                            .font(.caption.weight(.semibold))
                            .lineLimit(1)
                        Spacer()
                        Text(cabMergeStatusText(finding.status))
                            .font(.caption2)
                            .foregroundColor(cabMergeStatusColor(finding.status))
                    }
                    Text("\(finding.sourceArchive) · \(finding.sourceResourcePath)")
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                    Text("Nguồn node \(finding.sourceNodePath) · class-142 pathID \(finding.sourceObjectPathID) · \(finding.sourceContainerKeyCount) keys · SerializedFile v\(finding.sourceSerializedFileFormatVersion)")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    if let destinationPath = finding.destinationResourcePath {
                        Text("RES đích: \(destinationPath) · node \(finding.destinationNodePath ?? "?") · pathID \(finding.destinationObjectPathID ?? 0) · v\(finding.destinationSerializedFileFormatVersion ?? 0)")
                            .font(.system(size: 9, design: .monospaced))
                            .foregroundColor(.secondary)
                            .textSelection(.enabled)
                    }
                    metric("Key MOD-only · pointer khác · key trùng",
                           "\(finding.legacyOnlyContainerKeyCount) · \(finding.changedAssetPointerCount) · \(finding.duplicateContainerKeyCount)")
                    if !finding.dependenciesMissingFromDestination.isEmpty {
                        Text("Dependency cần xác minh: " + finding.dependenciesMissingFromDestination.prefix(6).joined(separator: ", "))
                            .font(.caption2)
                            .foregroundColor(.orange)
                    }
                    Text(finding.reason)
                        .font(.caption2)
                        .foregroundColor(finding.status == .candidate ? .secondary : .orange)
                }
                .padding(.vertical, 5)
                Divider().overlay(.white.opacity(0.08))
            }
            if findings.count > 14 {
                Text("Hiển thị 14 / \(findings.count) bản ghi CAB.")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Text("Khớp bundle identity mới chỉ là bước preflight. Chưa có nghĩa object đã nhập hoặc dependency/checksum đã sẵn sàng để xuất.")
                .font(.caption2.weight(.semibold))
                .foregroundColor(.orange)
        }
    }

    private func cabMergeStatusText(_ status: AoVRemakeCABMergeStatus) -> String {
        switch status {
        case .candidate: return "Khớp ứng viên"
        case .missingDestination: return "Thiếu CAB đích"
        case .ambiguousDestination: return "Trùng identity"
        case .incompatibleSerializedFile: return "Khác format/platform"
        case .duplicateContainerKeys: return "Key bị trùng"
        }
    }

    private func cabMergeStatusColor(_ status: AoVRemakeCABMergeStatus) -> Color {
        status == .candidate ? .green : .orange
    }

    private func catalogPlanStatusText(_ status: AoVRemakeCatalogPlanStatus) -> String {
        switch status {
        case .candidate: return "Ứng viên"
        case .requiresExternalTargetAudit: return "Cần audit CAB"
        case .noChanges: return "Không đổi"
        case .missingCurrentBundle: return "Thiếu trong RES"
        case .ambiguousCurrentBundle: return "Trùng tên"
        case .blocked: return "Bị chặn"
        }
    }

    private func catalogPlanStatusColor(_ status: AoVRemakeCatalogPlanStatus) -> Color {
        switch status {
        case .candidate: return .green
        case .requiresExternalTargetAudit, .blocked, .missingCurrentBundle, .ambiguousCurrentBundle: return .orange
        case .noChanges: return .secondary
        }
    }

    private func catalogStatusText(_ status: AoVRemakeCatalogRecordStatus) -> String {
        switch status {
        case .mappedCandidate: return "Có ứng viên"
        case .missingFromCurrent: return "Thiếu ở RES"
        case .ambiguousCurrentMatch: return "Trùng tên"
        }
    }

    private var homeDashboard: some View {
        GeometryReader { geometry in
            ScrollView(showsIndicators: false) {
                VStack(spacing: 12) {
                    // The supplied hero artwork is used as both the full-screen atmosphere
                    // and the top brand panel; controls remain real SwiftUI controls.
                    ZStack(alignment: .bottomLeading) {
                        Image("AkiHero")
                            .resizable()
                            .scaledToFill()
                            .frame(width: geometry.size.width, height: min(geometry.size.height * 0.30, 250))
                            .clipped()
                        LinearGradient(
                            colors: [.clear, Color.black.opacity(0.16), Color.black.opacity(0.70)],
                            startPoint: .center, endPoint: .bottom
                        )
                        VStack(alignment: .leading, spacing: 4) {
                            Text("AKI AOV SKIN")
                                .font(.system(size: 25, weight: .black, design: .rounded))
                                .tracking(1.2)
                            Text("Gộp Skin · Trải Nghiệm Đỉnh Cao")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundStyle(.white.opacity(0.88))
                        }
                        .padding(.horizontal, 20)
                        .padding(.bottom, 15)
                    }
                    .frame(height: min(geometry.size.height * 0.30, 250))
                    .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 24, style: .continuous)
                        .stroke(LinearGradient(colors: [.white.opacity(0.65), .purple.opacity(0.65), .white.opacity(0.12)],
                                               startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1.2))
                    .shadow(color: .purple.opacity(0.42), radius: 22, y: 8)

                    homeSelectionCard(
                        title: "Chọn File ZIP",
                        subtitle: "Hỗ trợ gói nền hoặc skin AOV",
                        status: selectedURLs.isEmpty ? "Chưa chọn file" : "\(selectedURLs.count) ZIP đã chọn",
                        icon: "folder.fill",
                        actionTitle: "Chọn File"
                    ) { importPurpose = .merger; isImporting = true }

                    homeSelectionCard(
                        title: "Chọn Gói Nền",
                        subtitle: "Tùy chọn gói nền yêu thích",
                        status: selectedBaseURL?.lastPathComponent ?? "Chưa chọn gói nền",
                        icon: "square.stack.3d.up.fill",
                        actionTitle: "Chọn Gói"
                    ) {
                        if selectedURLs.isEmpty {
                            importPurpose = .merger
                            isImporting = true
                        } else {
                            showBasePicker = true
                        }
                    }

                    HStack(spacing: 7) {
                        benefitTile("Xử Lý Nhanh", detail: "Trên iPhone", icon: "bolt.fill")
                        benefitTile("An Toàn", detail: "Kiểm tra CRC", icon: "checkmark.shield.fill")
                        benefitTile("Mượt Mà", detail: "Giữ file nguồn", icon: "diamond.fill")
                        benefitTile("Cập Nhật", detail: "Kiểm tra bản mới", icon: "arrow.clockwise")
                    }

                    Button {
                        if selectedURLs.count >= 2 {
                            runModMerge()
                        } else if selectedURLs.isEmpty {
                            importPurpose = .merger
                            isImporting = true
                        } else {
                            selectedTab = "Tính năng"
                        }
                    } label: {
                        HStack(spacing: 14) {
                            Image(systemName: "wrench.and.screwdriver.fill")
                                .font(.system(size: 30, weight: .black))
                            VStack(alignment: .leading, spacing: 4) {
                                Text(isMerging ? "ĐANG GHÉP SKIN" : "BẮT ĐẦU GHÉP SKIN")
                                    .font(.system(size: 18, weight: .black, design: .rounded))
                                Text(selectedURLs.count >= 2 ? "Tạo file ZIP hoàn chỉnh" : "Chọn file ZIP để bắt đầu")
                                    .font(.system(size: 12, weight: .semibold))
                            }
                            Spacer(minLength: 4)
                            Image(systemName: "chevron.right.2")
                                .font(.system(size: 22, weight: .black))
                        }
                        .foregroundStyle(Color(red: 0.055, green: 0.035, blue: 0.12))
                        .padding(.horizontal, 20)
                        .frame(maxWidth: .infinity, minHeight: 92)
                        .background(
                            LinearGradient(colors: [.white, Color(red: 0.78, green: 0.70, blue: 1)],
                                           startPoint: .topLeading, endPoint: .bottomTrailing),
                            in: RoundedRectangle(cornerRadius: 23, style: .continuous)
                        )
                        .overlay(RoundedRectangle(cornerRadius: 23, style: .continuous)
                            .stroke(.white.opacity(0.98), lineWidth: 1.8))
                        .shadow(color: .purple.opacity(0.65), radius: 22, y: 8)
                    }
                    .buttonStyle(.plain)
                    .disabled(isMerging)

                    HStack(spacing: 12) {
                        Image(systemName: "megaphone.fill")
                            .font(.system(size: 25, weight: .bold))
                            .foregroundStyle(Color(red: 0.85, green: 0.77, blue: 1))
                            .frame(width: 42)
                        VStack(alignment: .leading, spacing: 4) {
                            Text("Thông Báo")
                                .font(.system(size: 15, weight: .bold, design: .rounded))
                                .foregroundStyle(.white)
                            Text("Chào mừng bạn đến với AKI AOV SKIN!")
                                .font(.system(size: 12, weight: .medium))
                                .foregroundStyle(.white.opacity(0.78))
                        }
                        Spacer(minLength: 2)
                        Button { selectedTab = "Thông tin" } label: {
                            Label("Xem Thêm", systemImage: "chevron.right")
                                .font(.system(size: 11, weight: .semibold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 12).padding(.vertical, 10)
                                .background(.white.opacity(0.13), in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(15)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 21, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 21, style: .continuous)
                        .stroke(.white.opacity(0.32), lineWidth: 1))
                }
                .padding(.horizontal, 13)
                .padding(.top, 6)
                .padding(.bottom, 22)
                .frame(maxWidth: .infinity)
            }
            .background {
                ZStack {
                    Image("AkiHero")
                        .resizable()
                        .scaledToFill()
                        .frame(width: geometry.size.width, height: geometry.size.height)
                        .clipped()
                        .blur(radius: 22)
                        .overlay(Color(red: 0.025, green: 0.018, blue: 0.065).opacity(0.78))
                    LinearGradient(
                        colors: [Color.black.opacity(0.38), Color.purple.opacity(0.20), Color.black.opacity(0.76)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    )
                }
                .ignoresSafeArea()
            }
        }
    }

    private func homeSelectionCard(title: String, subtitle: String, status: String,
                                   icon: String, actionTitle: String,
                                   action: @escaping () -> Void) -> some View {
        HStack(spacing: 11) {
            Image(systemName: icon)
                .font(.system(size: 30, weight: .medium))
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(Color(red: 0.78, green: 0.70, blue: 1))
                .frame(width: 48)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 17, weight: .bold, design: .rounded)).foregroundStyle(.white)
                Text(subtitle).font(.system(size: 10, weight: .medium)).foregroundStyle(.white.opacity(0.72))
                    .lineLimit(2)
                HStack(spacing: 5) {
                    Image(systemName: "checkmark.seal.fill")
                    Text(status)
                }
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Color(red: 0.78, green: 0.60, blue: 1))
                .lineLimit(1)
            }
            Spacer(minLength: 2)
            Button(action: action) {
                HStack(spacing: 5) {
                    Image(systemName: "arrow.up.doc.fill")
                    Text(actionTitle).lineLimit(1)
                    Image(systemName: "chevron.right.2")
                }
                .font(.system(size: 11, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)
                .padding(.horizontal, 10)
                .frame(width: 116, height: 48)
                .background(LinearGradient(colors: [Color(red: 0.48, green: 0.40, blue: 0.90), Color(red: 0.22, green: 0.16, blue: 0.45)],
                                           startPoint: .topLeading, endPoint: .bottomTrailing),
                            in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(.white.opacity(0.65), lineWidth: 1))
                .shadow(color: .purple.opacity(0.30), radius: 9, y: 4)
            }
            .buttonStyle(.plain)
        }
        .padding(13)
        .background(LinearGradient(colors: [Color.white.opacity(0.10), Color.purple.opacity(0.08), Color.black.opacity(0.34)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing),
                    in: RoundedRectangle(cornerRadius: 21, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 21, style: .continuous)
            .stroke(LinearGradient(colors: [.white.opacity(0.40), .purple.opacity(0.42), .white.opacity(0.12)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1))
        .shadow(color: .black.opacity(0.26), radius: 13, y: 6)
    }

    private func benefitTile(_ title: String, detail: String, icon: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: icon).font(.system(size: 20, weight: .bold))
                .foregroundStyle(Color(red: 0.90, green: 0.86, blue: 1))
                .shadow(color: .purple.opacity(0.75), radius: 8)
            Text(title).font(.system(size: 9, weight: .heavy, design: .rounded))
                .foregroundStyle(.white).lineLimit(1).minimumScaleFactor(0.75)
            Text(detail).font(.system(size: 8, weight: .medium))
                .foregroundStyle(.white.opacity(0.65)).lineLimit(1).minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, minHeight: 82)
        .padding(.horizontal, 3)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 15, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous)
            .stroke(.white.opacity(0.20), lineWidth: 1))
    }

    private func dashboardTile(_ title: String, detail: String, icon: String) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Image(systemName: icon).font(.system(size: 20, weight: .bold)).foregroundStyle(.white)
            Text(title).font(.system(size: 14, weight: .bold, design: .rounded)).foregroundStyle(.white)
            Text(detail).font(.system(size: 11, weight: .medium)).foregroundStyle(.white.opacity(0.58))
        }
        .frame(maxWidth: .infinity, minHeight: 94, alignment: .leading)
        .padding(13)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(.white.opacity(0.16), lineWidth: 1))
    }

    private var historyDashboard: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("LỊCH SỬ").font(.system(size: 10, weight: .black, design: .rounded)).tracking(2).foregroundStyle(.white.opacity(0.55))
                Text("Hoạt động gần đây").font(.system(size: 27, weight: .black, design: .rounded)).foregroundStyle(.white)
                VStack(spacing: 12) {
                    Image(systemName: "clock.arrow.circlepath").font(.system(size: 34, weight: .light)).foregroundStyle(.white.opacity(0.65))
                    Text("Chưa có lịch sử").font(.headline).foregroundStyle(.white)
                    Text("Các lần ghép chưa được lưu thành nhật ký trong phiên bản này.").font(.footnote).multilineTextAlignment(.center).foregroundStyle(.white.opacity(0.62))
                }.frame(maxWidth: .infinity).padding(.vertical, 42).panel()
            }.padding(16)
        }
    }

    private var infoDashboard: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("THÔNG TIN").font(.system(size: 10, weight: .black, design: .rounded)).tracking(2).foregroundStyle(.white.opacity(0.55))
                Text("AKI AOV SKIN").font(.system(size: 27, weight: .black, design: .rounded)).foregroundStyle(.white)
                VStack(alignment: .leading, spacing: 12) {
                    infoRow("Phiên bản", "0.5.3")
                    infoRow("Nền tảng", "iOS 26.5+")
                    infoRow("Xử lý file", "Trực tiếp trên iPhone")
                    infoRow("Máy chủ", "Không tải ZIP lên")
                    infoRow("Trạng thái", "Đang phát triển")
                }.panel()
                VStack(alignment: .leading, spacing: 8) {
                    Text("Lưu ý tương thích").font(.headline).foregroundStyle(.white)
                    Text("ZIP xuất ra đã qua kiểm tra cấu trúc và CRC. Chưa có xác nhận chạy thành công trong Liên Quân. Một số loại metadata và xung đột tài nguyên vẫn chưa được hỗ trợ.").font(.footnote).foregroundStyle(.white.opacity(0.68))
                }.panel()
                Button(action: checkUpdates) {
                    Label("Kiểm tra cập nhật", systemImage: "arrow.clockwise").frame(maxWidth: .infinity)
                }.buttonStyle(AKISecondaryButtonStyle())
                Text(updateMessage).font(.caption).foregroundStyle(.white.opacity(0.6))
            }.padding(16)
        }
    }

    private func infoRow(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title).foregroundStyle(.white.opacity(0.62))
            Spacer()
            Text(value).fontWeight(.semibold).foregroundStyle(.white)
        }.font(.subheadline)
    }

    private var quickStatus: some View {
        HStack(spacing: 10) {
            Image(systemName: "cpu.fill")
                .font(.system(size: 15, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
            VStack(alignment: .leading, spacing: 3) {
                Text("SKIN MERGER STUDIO")
                    .font(.system(size: 10, weight: .black, design: .rounded))
                    .tracking(1.4).foregroundStyle(.white.opacity(0.65))
                Text("Sẵn sàng xử lý trên thiết bị")
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
            }
            Spacer()
            Circle().fill(Color.green).frame(width: 7, height: 7)
            Text("LOCAL")
                .font(.system(size: 9, weight: .black, design: .rounded))
                .tracking(1).foregroundStyle(.white.opacity(0.72))
        }
        .padding(13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 17, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 17, style: .continuous).stroke(.white.opacity(0.16), lineWidth: 1))
    }

    private var bottomNavigation: some View {
        HStack(spacing: 4) {
            navItem("Trang chủ", icon: "house.fill")
            navItem("Tính năng", icon: "square.grid.2x2")
            navItem("Lịch sử", icon: "clock.arrow.circlepath")
            navItem("Thông tin", icon: "crown.fill")
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 7)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 26, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 26, style: .continuous)
            .stroke(LinearGradient(colors: [.white.opacity(0.30), .purple.opacity(0.38), .white.opacity(0.12)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1))
        .shadow(color: .black.opacity(0.28), radius: 14, y: 5)
        .padding(.horizontal, 10)
        .padding(.bottom, 3)
        .background(Color.black.opacity(0.12).ignoresSafeArea(edges: .bottom))
    }

    private func navItem(_ title: String, icon: String) -> some View {
        Button {
            selectedTab = title
        } label: {
            VStack(spacing: 5) {
                Image(systemName: icon).font(.system(size: 18, weight: .semibold))
                Text(title).font(.system(size: 9, weight: .bold, design: .rounded))
                    .lineLimit(1).minimumScaleFactor(0.8)
                Capsule().fill(selectedTab == title ? Color(red: 0.74, green: 0.62, blue: 1) : .clear)
                    .frame(width: 28, height: 3)
            }
            .foregroundStyle(selectedTab == title ? Color.white : Color.white.opacity(0.48))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 7)
            .background {
                if selectedTab == title {
                    RoundedRectangle(cornerRadius: 19, style: .continuous)
                        .fill(LinearGradient(colors: [Color.white.opacity(0.16), Color.purple.opacity(0.16)],
                                             startPoint: .topLeading, endPoint: .bottomTrailing))
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var header: some View {
        ZStack(alignment: .topTrailing) {
            RoundedRectangle(cornerRadius: 25, style: .continuous)
                .fill(LinearGradient(
                    colors: [Color(red: 0.13, green: 0.12, blue: 0.22),
                             Color(red: 0.055, green: 0.055, blue: 0.105),
                             Color(red: 0.12, green: 0.09, blue: 0.20)],
                    startPoint: .topLeading, endPoint: .bottomTrailing
                ))
            Circle().stroke(Color.white.opacity(0.13), lineWidth: 1)
                .frame(width: 190, height: 190).offset(x: 68, y: -70)
            Circle().stroke(Color.purple.opacity(0.22), lineWidth: 1)
                .frame(width: 240, height: 240).offset(x: 88, y: -92)
            VStack(alignment: .leading, spacing: 13) {
                HStack {
                    Image(systemName: "crown.fill")
                        .font(.system(size: 13, weight: .black))
                        .foregroundStyle(Color.white)
                    Text("AKI / GAMING WORKBENCH")
                        .font(.system(size: 10, weight: .heavy, design: .rounded))
                        .tracking(2.1).foregroundStyle(.white.opacity(0.75))
                    Spacer()
                    Image(systemName: "sparkles")
                        .font(.system(size: 20, weight: .medium))
                        .foregroundStyle(.white.opacity(0.8))
                }
                HStack(spacing: 12) {
                    ZStack {
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(.white.opacity(0.10)).frame(width: 58, height: 58)
                        Image(systemName: "bolt.shield.fill")
                            .font(.system(size: 29, weight: .black)).foregroundStyle(.white)
                            .shadow(color: .purple.opacity(0.65), radius: 10)
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        Text("AKI AOV SKIN")
                            .font(.system(size: 27, weight: .black, design: .rounded))
                            .tracking(-0.8).foregroundStyle(.white)
                        Text("ULTIMATE MOD WORKSPACE")
                            .font(.system(size: 8, weight: .heavy, design: .rounded))
                            .tracking(1.5).foregroundStyle(.white.opacity(0.58))
                    }
                }
                Text("GỘP SKIN · TRẢI NGHIỆM ĐỈNH CAO")
                    .font(.system(size: 10, weight: .bold, design: .rounded))
                    .tracking(1.2).foregroundStyle(Color.white.opacity(0.72))
                Text("Bản iOS 0.5.3 · yêu cầu iOS 26.5+")
                    .font(.caption2.weight(.semibold)).foregroundStyle(.white.opacity(0.58))
                Text("Xử lý file ngay trên iPhone. Bộ ghép sẽ dừng khi gặp xung đột chưa có adapter đã xác minh.")
                    .font(.footnote).foregroundStyle(.white.opacity(0.78))
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(19)
        }
        .frame(maxWidth: .infinity, minHeight: 205, alignment: .leading)
        .clipShape(RoundedRectangle(cornerRadius: 25, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 25, style: .continuous)
            .stroke(LinearGradient(colors: [.white.opacity(0.55), .purple.opacity(0.32), .white.opacity(0.12)],
                                   startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1))
        .shadow(color: .purple.opacity(0.18), radius: 24, y: 12)
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
            if !report.unityFSBundles.isEmpty ||
                report.unityFSUnrecognizedCount > 0 ||
                report.unityFSTooLargeCount > 0 ||
                !report.unityFSInspectionFailures.isEmpty {
                Divider().overlay(.white.opacity(0.12))
                Label("UnityFS / CAB đã quét metadata", systemImage: "shippingbox.fill")
                    .font(.subheadline.weight(.semibold))
                metric("UnityFS / CAB nhận diện", "\(report.unityFSBundles.count)")
                metric("AssetBundle không phải UnityFS", "\(report.unityFSUnrecognizedCount)")
                metric("Quá giới hạn kiểm tra", "\(report.unityFSTooLargeCount)")
                ForEach(report.unityFSBundles.prefix(40)) { bundle in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(bundle.path)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                        Text("Unity \(bundle.engineVersion) · format v\(bundle.formatVersion) · \(bundle.blockCount) block · \(bundle.nodeCount) node")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                        Text("Block info: \(bundle.blockInfoCompression) · \(bundle.blockInfoAtEnd ? "ở cuối file" : "sau header") · dữ liệu khai báo \(formatByteCount(bundle.totalUncompressedBytes))")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                        ForEach(bundle.nodePathSamples, id: \.self) { nodePath in
                            Text(nodePath)
                                .font(.caption2.monospaced())
                                .foregroundColor(.secondary)
                                .textSelection(.enabled)
                        }
                        if let status = bundle.catalogExtractionStatus {
                            Text("Node audit: \(status)")
                                .font(.caption2)
                                .foregroundColor(status.hasPrefix("Đã trích xuất") ? .green : .orange)
                                .textSelection(.enabled)
                        }
                        ForEach(bundle.catalogNodeSamples) { node in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(node.path) · \(formatByteCount(node.size))")
                                    .font(.caption2.monospaced())
                                    .textSelection(.enabled)
                                if !node.prefixHex.isEmpty {
                                    Text("Prefix: \(node.prefixHex)")
                                        .font(.caption2.monospaced())
                                        .foregroundColor(.secondary)
                                }
                                if let summary = node.serializedFileSummary {
                                    Text(summary)
                                        .font(.caption2)
                                        .foregroundColor(summary.hasPrefix("SerializedFile") ? .green : .orange)
                                        .textSelection(.enabled)
                                }
                                ForEach(node.stringHints, id: \.self) { hint in
                                    Text("Chuỗi đọc được: \(hint)")
                                        .font(.caption2)
                                        .foregroundColor(.secondary)
                                        .textSelection(.enabled)
                                }
                            }
                        }
                    }
                    .padding(.vertical, 5)
                    Divider()
                }
            }
            if !report.unityFSInspectionFailures.isEmpty {
                Text("UnityFS chưa đọc được").font(.subheadline.weight(.semibold))
                ForEach(Array(report.unityFSInspectionFailures.enumerated()), id: \.offset) { _, failure in
                    Text(failure).font(.caption2).foregroundColor(.orange).textSelection(.enabled)
                }
            }
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
            Text("UnityFS hiện đã đọc header, block table, giải nén block none/LZ4/LZ4HC và trích xuất node read-only cho hai catalog Packer/Verification. App chưa giải mã type tree/field catalog, chưa cập nhật dependency/checksum và chưa chứng minh game sẽ nạp được bundle sau khi ghép.")
                .font(.footnote)
                .foregroundColor(.orange)
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

    private func importFiles(_ result: Result<[URL], Error>, purpose: ZIPImportPurpose = .merger) {
        switch result {
        case .success(let urls):
            guard !urls.isEmpty else { return }
            if purpose == .remakeBase && urls.count != 1 {
                alertText = "Chế độ RMK chỉ nhận đúng một ZIP làm RES nền."
                showAlert = true
                return
            }

            let existingURLs: [URL]
            let subfolder: String?
            switch purpose {
            case .merger:
                existingURLs = selectedURLs
                subfolder = nil
            case .remakeBase:
                existingURLs = []
                subfolder = "RemakeRES"
            case .legacyMods:
                existingURLs = remakeMODURLs
                subfolder = "LegacyMODs"
            }
            let existingCount = existingURLs.count
            let existingBytes = existingURLs.reduce(UInt64(0)) { partial, url in
                let size = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                let sum = partial.addingReportingOverflow(UInt64(max(size, 0)))
                return sum.overflow ? UInt64.max : sum.partialValue
            }
            let limit = purpose == .remakeBase ? 1 : AoVSourceFileImporter.maximumFiles
            guard existingCount + urls.count <= limit else {
                alertText = purpose == .remakeBase
                    ? "Chỉ chọn một ZIP RES nền."
                    : "App nhận tối đa 10 ZIP trong danh sách này. Hãy xóa bớt file rồi chọn lại."
                showAlert = true
                return
            }

            // Acquire provider access before dispatching file copies off the main actor.
            let securityScopeLease = AoVSecurityScopedAccess(urls: urls)
            isCopyingFiles = true
            Task {
                do {
                    let folder = try sourceFolder(subfolder: subfolder)
                    let report = try await Task.detached(priority: .userInitiated) {
                        try AoVSourceFileImporter.copy(
                            urls,
                            into: folder,
                            existingFileCount: existingCount,
                            existingBytes: existingBytes,
                            securityScopeLease: securityScopeLease
                        )
                    }.value

                    switch purpose {
                    case .merger:
                        selectedURLs.append(contentsOf: report.importedURLs)
                        analysis = nil
                        aesReport = nil
                        mergeReport = nil
                    case .remakeBase:
                        if let imported = report.importedURLs.first {
                            remakeBaseURL = imported
                            remakeReport = nil
                            remakeExportReport = nil
                        }
                    case .legacyMods:
                        remakeMODURLs.append(contentsOf: report.importedURLs)
                        remakeReport = nil
                        remakeExportReport = nil
                    }

                    if !report.errors.isEmpty {
                        alertText = "Đã nhập \(report.importedURLs.count)/\(urls.count) file. File lỗi:\n" +
                            report.errors.joined(separator: "\n")
                        showAlert = true
                    } else if report.importedURLs.isEmpty {
                        alertText = "Chưa có ZIP nào được thêm vào danh sách."
                        showAlert = true
                    } else {
                        let destination = subfolder.map { "SourceArchives/\($0)" } ?? "SourceArchives"
                        alertText = "Đã nhập thành công \(report.importedURLs.count) ZIP. Bản sao nằm trong Tệp → Trên iPhone → AKI AOV SKIN → \(destination)."
                        showAlert = true
                    }
                } catch {
                    alertText = error.localizedDescription
                    showAlert = true
                }
                isCopyingFiles = false
            }
        case .failure(let error):
            alertText = error.localizedDescription
            showAlert = true
        }
    }

    private func sourceFolder(subfolder: String? = nil) throws -> URL {
        let docs = try FileManager.default.url(for: .documentDirectory, in: .userDomainMask,
                                               appropriateFor: nil, create: true)
        var folder = docs.appendingPathComponent("SourceArchives", isDirectory: true)
        if let subfolder {
            folder.appendPathComponent(subfolder, isDirectory: true)
        }
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
            metric("ZIP đầu vào", formatByteCount(report.inputBytes))
            metric("ZIP đầu ra", formatByteCount(report.outputBytes))
            metric("Thời gian xử lý", String(format: "%.2f giây", report.elapsedSeconds))
            metric("Tài nguyên mới", "\(report.addedResources)")
            metric("Tài nguyên thay thế", "\(report.replacedResources)")
            metric("CommonActions đã ghép", "\(report.commonActionsFiles.count)")
            metric("Bảng MSES đã ghép", "\(report.mergedMSESTables.count)")
            metric("IFix patch đã ghép", "\(report.mergedIFixPatches.count)")
            metric("AssetRef duy nhất giữ nguyên", "\(report.preservedAssetReferenceSets.count)")
            metric("Wwise BNK thêm nguyên trạng (append-only)", "\(report.preservedWwiseBanks.count)")
            VStack(alignment: .leading, spacing: 8) {
                Text("Nhật ký xử lý")
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(.white)
                ForEach(report.pipelineEvents) { event in
                    HStack(alignment: .top, spacing: 9) {
                        Image(systemName: event.stage.systemImage)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.green)
                            .frame(width: 18)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(event.stage.title)
                                .font(.caption.weight(.semibold))
                                .foregroundColor(.white)
                            Text(event.detail)
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            .padding(.top, 5)
            ShareLink(item: report.outputURL) {
                Label("Chia sẻ / lưu ZIP kết quả", systemImage: "square.and.arrow.up")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(AKIPrimaryButtonStyle())
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
        guard selectedURLs.count >= 2 else {
            alertText = "Cần chọn ít nhất hai ZIP để ghép."
            showAlert = true
            return
        }
        guard let baseURL = selectedBaseURL else {
            alertText = "Hãy chọn một ZIP làm gói nền trước khi ghép. Các ZIP còn lại sẽ được dùng làm gói skin."
            showAlert = true
            return
        }
        isMerging = true
        mergeReport = nil
        let urls = selectedURLs
        Task {
            do {
                let report = try await Task.detached(priority: .userInitiated) {
                    try AoVModArchiveMerger.merge(urls, baseURL: baseURL)
                }.value
                mergeReport = report
            } catch {
                alertText = error.localizedDescription
                showAlert = true
            }
            isMerging = false
        }
    }

    private func runRemakeAnalysis() {
        guard let baseURL = remakeBaseURL else {
            alertText = "Hãy chọn một ZIP RES mới làm nền."
            showAlert = true
            return
        }
        guard !remakeMODURLs.isEmpty else {
            alertText = "Hãy thêm ít nhất một MOD cũ để đối chiếu."
            showAlert = true
            return
        }
        isAnalyzingRemake = true
        remakeReport = nil
        remakeExportReport = nil
        let legacyURLs = remakeMODURLs
        Task {
            do {
                let report = try await Task.detached(priority: .userInitiated) {
                    try AoVModRemakeAnalyzer.analyze(baseRESURL: baseURL, legacyMODURLs: legacyURLs)
                }.value
                remakeReport = report
            } catch {
                alertText = error.localizedDescription
                showAlert = true
            }
            isAnalyzingRemake = false
        }
    }

    private func runRemakeSafeExport() {
        guard let baseURL = remakeBaseURL, !remakeMODURLs.isEmpty, remakeReport != nil else {
            alertText = "Hãy chọn RES nền, MOD cũ và chạy phân tích trước."
            showAlert = true
            return
        }
        isExportingRemake = true
        remakeExportReport = nil
        let legacyURLs = remakeMODURLs
        Task {
            do {
                let report = try await Task.detached(priority: .userInitiated) {
                    try AoVModRemakeExporter.exportLegacyOnlyStandaloneResources(
                        baseRESURL: baseURL,
                        legacyMODURLs: legacyURLs
                    )
                }.value
                remakeExportReport = report
            } catch {
                alertText = error.localizedDescription
                showAlert = true
            }
            isExportingRemake = false
        }
    }

    private func remakeExportResultView(_ report: AoVModRemakeExportReport) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Đã tạo RES remake một phần", systemImage: "checkmark.seal.fill")
                .font(.headline)
                .foregroundColor(.green)
            Text(report.outputURL.lastPathComponent)
                .font(.subheadline.weight(.semibold))
                .textSelection(.enabled)
            metric("Tài nguyên mới được thêm", "\(report.addedResources)")
            metric("Packer entry mới", "\(report.packerAddedPaths.count)")
            metric("Verification ZIP mới", "\(report.verificationAddedZIPPaths.count)")
            metric("Verification Databin mới", "\(report.verificationAddedDatabinPaths.count)")
            metric("Dung lượng RES đầu ra", formatByteCount(report.outputBytes))
            Text("File nằm trong Tệp → Trên iPhone của tôi → AKI AOV SKIN → AKI AOV SKIN Exports. Chỉ cấu trúc ZIP và catalog đã được kiểm tra; chưa phải bản xác nhận chạy trong Liên Quân.")
                .font(.caption).foregroundColor(.orange)
        }
        .panel()
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

private struct AKIPrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.subheadline, design: .rounded).weight(.heavy))
            .foregroundStyle(Color(red: 0.055, green: 0.045, blue: 0.09))
            .padding(.vertical, 13)
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity)
            .background(
                LinearGradient(colors: [Color.white, Color(red: 0.83, green: 0.81, blue: 1.0)],
                               startPoint: .topLeading, endPoint: .bottomTrailing),
                in: RoundedRectangle(cornerRadius: 15, style: .continuous)
            )
            .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous)
                .stroke(Color.white.opacity(0.8), lineWidth: 1))
            .shadow(color: .purple.opacity(configuration.isPressed ? 0.08 : 0.25),
                    radius: configuration.isPressed ? 5 : 14, y: 5)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .opacity(configuration.isPressed ? 0.88 : 1)
    }
}

private struct AKISecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.subheadline, design: .rounded).weight(.semibold))
            .foregroundStyle(.white.opacity(0.94))
            .padding(.vertical, 12)
            .padding(.horizontal, 13)
            .frame(maxWidth: .infinity)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 15, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 15, style: .continuous)
                .stroke(Color.white.opacity(0.19), lineWidth: 1))
            .shadow(color: .black.opacity(0.18), radius: 10, y: 4)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .opacity(configuration.isPressed ? 0.82 : 1)
    }
}

private extension View {
    func panel() -> some View {
        self.padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                LinearGradient(colors: [
                    Color(red: 0.105, green: 0.095, blue: 0.16).opacity(0.96),
                    Color(red: 0.045, green: 0.045, blue: 0.085).opacity(0.98)
                ], startPoint: .topLeading, endPoint: .bottomTrailing),
                in: RoundedRectangle(cornerRadius: 20, style: .continuous)
            )
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous)
                .stroke(LinearGradient(colors: [.white.opacity(0.25), .purple.opacity(0.20), .white.opacity(0.08)],
                                       startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1))
            .shadow(color: .black.opacity(0.24), radius: 16, y: 8)
    }
}
