# OKR — Claude Code skills

Sáu skill cho Claude Code, đọc một codebase Rails và trả lời hai câu hỏi:
**"màn hình này chạm vào những file nào?"** và **"PR này làm hỏng cái gì?"**

Cài bằng cách copy thư mục `.claude/` vào repo cần phân tích (hoặc vào `~/.claude`
để dùng cho mọi project).

## KR1 — Impact map

Dựng bản đồ: từ route → controller#action (màn hình) → các file code nó thực sự
chạm tới.

| Bước | Skill | Chạy | Ra |
|---|---|---|---|
| 1 | `code-map` | `.claude/skills/code-map/scripts/scan-code-map.sh --root .` | `.ai/code-map/raw/scan.json` |
| 2 | `impact-analysis` | `.claude/skills/impact-analysis/scripts/analyze-impact.sh` | `.ai/code-map/analysis/impact.json` |
| 3 | `code-map-doc` | `.claude/skills/code-map-doc/scripts/build.rb --open` | `.ai/code-map/doc/index.html` + `summary.md` |

- **code-map** — quét routes, controllers, models, services, jobs, views, Packwerk
  packs, Vue pages và các constant reference giữa chúng. Chỉ đọc và ghi lại, không
  suy diễn. Refresh nhanh bằng `--since <commit>`.
- **impact-analysis** — nối dữ liệu thô thành đồ thị: mỗi màn hình chạm file nào,
  file nào bị nhiều màn hình dùng chung (hub), file nào không màn hình nào chạm
  tới, route nào trỏ vào controller không tồn tại.
- **code-map-doc** — xuất một trang HTML tự chứa: cây theo màn hình, có filter theo
  module, ô search và panel chi tiết. Tên màn hình hiển thị tiếng Việt thay cho
  `controller#action`.

Shortcut: `/map` (xem `.claude/commands/map.md`) dựng cả hai file và mở trang HTML.

## KR2 — Review PR theo blast radius

| Bước | Skill | Chạy | Ra |
|---|---|---|---|
| 1 | `pr-diff` | `.claude/skills/pr-diff/scripts/scan-pr-diff.sh` | `.ai/code-map/raw/pr-diff.json` |
| 2 | `blast-radius` | `.claude/skills/blast-radius/scripts/analyze-blast-radius.sh` | `.ai/code-map/analysis/blast-radius.json` |
| 3 | `pr-check` | `.claude/skills/pr-check/scripts/check-pr.sh` | `.ai/code-map/analysis/pr-check.json` |

- **pr-diff** — đọc PR đổi những file nào, dòng nào, và mỗi dòng nằm trong
  class / module / def nào. Không đánh giá gì cả.
  Chế độ: mặc định (PR-equivalent), `--working`, `--staged`, `--pr <số>`.
- **blast-radius** — đối chiếu diff với `impact.json`: màn hình nào chạm tới file
  đã đổi, file nào là hub dùng chung, màn hình nào bị viết lại hẳn, file nào bản
  đồ chưa biết. Chỉ đọc `pr-diff.json` + `impact.json`, không đọc source.
- **pr-check** — tìm ba loại vấn đề: (a) làm hỏng chỗ khác (xóa/đổi tên method,
  class, action, export, i18n key vẫn còn được dùng; đổi signature mà chưa sửa
  caller), (b) code trùng hoặc chết, (c) vi phạm rule của team trong
  `.claude/rules` và `CLAUDE.local.md`. Script thu thập nghi vấn kèm bằng chứng,
  Claude đọc source xác nhận từng cái.

## Thứ tự phụ thuộc

```
code-map ──> impact-analysis ──> code-map-doc
                   │
pr-diff ───────────┴──> blast-radius ──> pr-check
```

`impact.json` phải có trước khi chạy `blast-radius`.
