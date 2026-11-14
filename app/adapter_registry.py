"""Explicit capability registry for safe multi-format AoV mod processing.

Detection and inspection do not imply merge support. Every merge-capable adapter
must be version-scoped and backed by parser/serializer validation and fixtures.
"""
from __future__ import annotations

from copy import deepcopy


_ADAPTERS = (
    {
        "id": "generic_zip",
        "name": "ZIP thông thường",
        "status": "merge_supported",
        "formats": ["ZIP"],
        "scope": "Tệp không xung đột hoặc trùng byte; không diễn giải tài nguyên game.",
        "merge_policy": "Giữ tệp mới; chỉ bỏ qua bản trùng khi SHA-256 giống nhau; từ chối xung đột khác nội dung.",
        "game_compatibility": "not_applicable",
    },
    {
        "id": "aov_kpm100_vtk_actor595_1.64.1",
        "name": "AoV iOS KPM100 + Vấn Tâm Kiếm Actor 595",
        "status": "merge_supported_version_locked",
        "formats": ["KPM100 direct ZIP", "KPM_MOD payload", "IFix", "MSES", "CommonActions", "Unity AssetBundle", "Wwise BNK"],
        "scope": "Chỉ hỗ trợ đúng cặp archive KPM100 1.64.1.12 + VTK 1.64.1.11 đã ghim SHA-256 trong bộ kiểm tra tích hợp; giữ cây tài nguyên nền và 99 skin còn lại, thay/ghép tài nguyên Actor 595.",
        "merge_policy": "IFix parse/serialize và remap chỉ số; 70 KPM_MOD payload round-trip; MSES theo allowlist từng bảng; CommonActions theo track; Packer/Verification theo dependency; từ chối mọi hash/phiên bản ngoài cặp đã ghim.",
        "game_compatibility": "structurally_validated; not_game_tested",
    },
    {
        "id": "aov_mod_to_mod_vtk_actor595_1.64.1",
        "name": "AoV iOS MOD 10 skin wrapper + Vấn Tâm Kiếm Actor 595",
        "status": "merge_supported_version_locked",
        "formats": ["AoV MOD wrapper 10 skin", "IFix", "MSES v7", "CommonActions", "Huanhua billboard MSES v7", "Unity AssetBundle", "Wwise iOS sound resources"],
        "scope": "Chỉ nhận MOD wrapper iOS có Resources.zip + DanhSachSkin.txt; Resources.zip phải có đúng cấu trúc nền 1.64.1.12 và các bảng đã kiểm tra. VTK iOS phải là bộ Actor 595 1.64.1.11 có đủ đúng 10 AssetBundle bắt buộc.",
        "merge_policy": "Ghép IFix, MSES v7 theo allowlist từng bảng (bao gồm ba bảng Huanhua billboard chỉ nhận khóa Actor 595 0x253), CommonActions XML/track, metadata Packer/Verification và ZIP lồng KernelLua/StableSystems bằng union theo SHA-256; chỉ thêm mục mới, bỏ bản trùng giống hệt và từ chối xung đột nội bộ khác payload. Kiểm tra checksum, CRC và inventory; từ chối sai phiên bản, thiếu/thừa bundle hoặc xung đột ngoài quy tắc.",
        "game_compatibility": "structurally_validated; not_game_tested",
    },
    {
        "id": "aov_p9_p10_skin_union_1.64.1",
        "name": "AoV iOS P9 + P10 · Ghép 19 skin",
        "status": "merge_supported_version_locked",
        "formats": [
            "P9/P10 MOD wrapper", "MSES v7", "IFix actor-scoped",
            "CommonActions actor tracks", "PackerInfo/ResourceVerification"
        ],
        "scope": (
            "Chỉ nhận đúng hai ZIP P9/P10 nguồn đã ghim SHA-256, cùng build 1.64.1.12 "
            "và đúng Actor_Action inventory (9 + 10 Actor)."
        ),
        "merge_policy": (
            "Hợp nhất resource union; MSES theo Actor-owner và schema nguồn; "
            "organSkin sửa stale row-count không sinh hàng; IFix actor-scoped; "
            "CommonActions track merge; PackerInfo chọn 410 entry theo Actor với "
            "dependency graph giữ nguyên. Mọi xung đột ngoài adapter đều chặn."
        ),
        "game_compatibility": "structurally_validated; not_game_tested",
    },
    {
        "id": "aov_multi_mod_intake",
        "name": "Phân tích nhiều loại MOD AoV",
        "status": "inspect_only",
        "formats": [
            "ZIP thông thường", "MOD wrapper", "Resources.zip lồng",
            "AoV AES/Zstandard", "MSES", "IFix", "CommonActions",
            "Unity AssetBundle", "Wwise BNK", "gói nhiều phần"
        ],
        "scope": (
            "Nhận và kiểm kê MOD không phụ thuộc tên P9/P10/TM/VTK; đối chiếu cấu trúc, "
            "đường dẫn, Actor ID, định dạng, phiên bản suy luận và xung đột tài nguyên."
        ),
        "merge_policy": (
            "Chỉ phân tích. Không phải adapter ghép; MOD chưa có parser/serializer, "
            "quy tắc tương thích và fixture vẫn bị chặn xuất ZIP."
        ),
        "game_compatibility": "inspection_only; not_game_tested",
    },
    {
        "id": "aov_multi_actor_auto_detect",
        "name": "AoV nhiều MOD · tự nhận diện Actor (ghép có điều kiện)",
        "status": "merge_supported_conditional",
        "formats": [
            "MOD wrapper + Resources.zip", "MSES v7 đã có schema",
            "IFix có thể parse/round-trip", "CommonActions SkinOrAvatarList trực tiếp",
            "Unity AssetBundle resource mới + Packer/Verification metadata",
            "ZIP package lồng không có xung đột"
        ],
        "scope": (
            "Chỉ nhận ít nhất hai MOD AoV cùng version.txt, có Actor ID được nhận diện "
            "và ít nhất một wrapper Resources.zip + DanhSachSkin.txt. Một số tài nguyên "
            "được union khi đường dẫn mới; xung đột chỉ ghép nếu parser tương ứng xác minh."
        ),
        "merge_policy": (
            "MSES dùng schema/Actor-owner đã hỗ trợ; CommonActions chỉ nhận SkinOrAvatarList "
            "trực tiếp; IFix phải parse và round-trip; AssetBundle mới cần Packer và "
            "Verification được UnityPy đọc/ghi/đọc lại. AssetBundle trùng đường dẫn khác "
            "payload, Wwise BNK, IFix/CommonActions không tương thích, version/schema lạ "
            "và mọi xung đột chưa có parser đều bị chặn. Không tuyên bố hỗ trợ mọi skin."
        ),
        "game_compatibility": "structurally_validated; not_game_tested",
    },
    {
        "id": "aov_wrapper_inspection",
        "name": "AoV AES / Zstandard wrapper",
        "status": "inspect_only",
        "formats": ["AoV AES", "AoV AES + Zstandard", "AoV Zstandard wrapper", "Zstandard"],
        "scope": "Nhận diện chữ ký và kiểm kê; chưa có giải mã/ghi lại tổng quát cho mọi build.",
        "merge_policy": "Không sửa hoặc ghép payload khi chưa xác định đúng thuật toán, key, dictionary và phiên bản.",
        "game_compatibility": "not_tested",
    },
    {
        "id": "aov_mses",
        "name": "MSES tables",
        "status": "inspect_only_except_locked_vtk_adapter",
        "formats": ["BattleBank.bytes", "LobbyBank.bytes", "skillcombine.bytes", "ResSkinSeniorLabelCfg.bytes"],
        "scope": (
            "MSES v7 parser/serializer hỗ trợ adapter VTK Actor 595 đã khóa phiên bản "
            "và helper cấp bảng P9/P10 ghim SHA-256/Actor_Action cho heroSkin, "
            "ResSkinMotionBaseCfg, HeroSkinShop, skillmark, BattleBank, LobbyBank, "
            "LobbySound, ChatSound, CoupleSound và HeroSound; HeroSound dùng prefix ID đa độ dài nhưng chỉ nhận một Actor khớp duy nhất."
        ),
        "merge_policy": (
            "Helper P9/P10 là cấp bảng, chưa phải quyền ghép ZIP nhiều wrapper. "
            "Không nối byte hoặc thay bảng giữa phiên bản tùy ý; Wwise BNK vẫn cần "
            "remap Event/Action/HIRC/WEM riêng."
        ),
        "game_compatibility": "not_tested",
    },
    {
        "id": "aov_ifix",
        "name": "IFix",
        "status": "inspect_only_except_locked_vtk_adapter",
        "formats": ["HeroSkin_1.bytes", "HeroSkin_2.bytes", "IFix"],
        "scope": "Remap chỉ mục chỉ được hỗ trợ trong adapter Vấn Tâm Kiếm đã khóa phiên bản.",
        "merge_policy": "Không gộp IFix từ actor/build khác khi chưa có schema và fixture tương ứng.",
        "game_compatibility": "not_tested",
    },
    {
        "id": "unity_assetbundle",
        "name": "Unity AssetBundle",
        "status": "inspect_only",
        "formats": ["UnityFS", "UnityRaw", "UnityWeb", "AssetBundle"],
        "scope": "Đọc cấu trúc và kiểm tra round-trip giới hạn kích thước; chưa có bộ hợp nhất object/dependency tổng quát.",
        "merge_policy": "Không ghép hoặc ghi lại bundle chỉ vì cùng Actor ID hay cùng tên file.",
        "game_compatibility": "not_tested",
    },
    {
        "id": "wwise_bnk",
        "name": "Wwise SoundBank",
        "status": "inspect_only",
        "formats": ["BKHD", "DIDX", "DATA", "HIRC", "BNK"],
        "scope": "Kiểm tra chunk, media ID và HIRC object trong giới hạn kích thước.",
        "merge_policy": "Không nối byte các BNK; cần adapter remap media/event và kiểm tra tham chiếu.",
        "game_compatibility": "not_tested",
    },
    {
        "id": "text_xml_json_image_audio",
        "name": "Tài nguyên văn bản, ảnh và âm thanh rời",
        "status": "inspect_only",
        "formats": ["XML", "JSON", "PNG", "JPEG", "WAV", "Ogg", "GZIP"],
        "scope": "Nhận diện định dạng và kiểm kê; chưa có quy tắc tương thích AoV tổng quát.",
        "merge_policy": "Không tự ghi đè cấu hình, UI, hiệu ứng hoặc âm thanh.",
        "game_compatibility": "not_tested",
    },
)


def get_adapter_capabilities() -> dict:
    """Return a detached, JSON-safe snapshot for API/UI consumers."""
    adapters = deepcopy(list(_ADAPTERS))
    return {
        "schema_version": 1,
        "policy": "fail_closed",
        "merge_any_mod_claim": False,
        "game_tested": False,
        "adapters": adapters,
        "summary": {
            "merge_supported": sum(item["status"] == "merge_supported" for item in adapters),
            "version_locked": sum(item["status"] == "merge_supported_version_locked" for item in adapters),
            "blocked": sum(item["status"].startswith("blocked_") for item in adapters),
            "inspection_only": sum(item["status"].startswith("inspect_only") for item in adapters),
        },
    }
