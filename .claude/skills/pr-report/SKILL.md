---
name: pr-report
description: >-
  Merge everything KR2 found about a pull request into one report a reviewer can read
  top to bottom: a risk verdict (high / medium / low) with the reasons that produced it,
  what changed, how far the change reaches (screens, packs, shared files), every finding
  from pr-check grouped by kind with a slot for Claude's confirmation, the order the files
  should be read in, and the limits of the machine steps. Step 4 of KR2 — it reads only
  pr-diff.json, blast-radius.json and pr-check.json, never source and never git, and it
  invents no new findings. Use for "write up this PR review", "is this PR risky",
  "summarise the review", or after pr-check.
---

# pr-report — KR2 step 4: gộp thành 1 báo cáo, xếp loại rủi ro

| Step | Skill | Output |
|---|---|---|
| 1 | `pr-diff` | `.ai/code-map/raw/pr-diff.json` — dòng nào đổi |
| 2 | `blast-radius` | `.ai/code-map/analysis/blast-radius.json` — đụng bao nhiêu màn hình |
| 3 | `pr-check` | `.ai/code-map/analysis/pr-check.json` — có sai không |
| 4 | **pr-report** | `.ai/code-map/doc/pr-report.md` + `analysis/pr-report.json` — đọc gì, theo thứ tự nào |

## Run

```bash
.claude/skills/pr-report/scripts/build-pr-report.sh
.claude/skills/pr-report/scripts/build-pr-report.sh --lang en
.claude/skills/pr-report/scripts/build-pr-report.sh --verdicts /tmp/verdicts.json
```

Chỉ `pr-diff.json` là bắt buộc. Thiếu `blast-radius.json` hoặc `pr-check.json` thì
báo cáo vẫn dựng được, mục tương ứng bị bỏ và ghi rõ lý do ở đầu file — **không im lặng**.

## Xếp loại rủi ro

Đây là toàn bộ luật. Không có điểm số ẩn nào khác.

| Mức | Khi nào |
|---|---|
| **cao** | có `error`, **hoặc** file dùng chung mức `critical`/`high` + có `warning` |
| **vừa** | file dùng chung mức `critical`/`high`, **hoặc** có `warning` |
| **thấp** | không có cả hai |
| **chưa kết luận** | chưa chạy `pr-check` — không có dữ liệu vi phạm thì không được nói là an toàn |

Số màn hình bị viết lại (`screens_rewritten`) luôn được nêu trong phần lý do nhưng
**không tự nâng mức** — viết lại một màn hình ít người dùng không nguy hiểm bằng
sửa một dòng trong model mà 800 màn hình chạm tới.

Muốn đổi ngưỡng: sửa `verdict()` và `WIDE_RISKS` trong `scripts/build_pr_report.py`.

## Thứ tự đọc

Bảng "Đọc theo thứ tự này" sắp xếp theo: số `error` giảm dần → mức rủi ro file
(`critical` → `low`) → số màn hình chạm tới giảm dần → số `warning` giảm dần.
File có phát hiện nhưng không có trong bản đồ KR1 (spec chẳng hạn) vẫn được đưa vào
cuối bảng, không bị rơi.

## Gắn kết luận của Claude vào báo cáo

Sau step 3, Claude đọc source để xác nhận từng `error` / `warning`. Ghi kết quả ra
một file JSON rồi dựng lại báo cáo — **đừng sửa tay vào `pr-report.md`**, lần chạy sau sẽ ghi đè.

```json
{
  "spec_mock_stub-969b6936": { "verdict": "confirmed", "note": "allow_any_instance_of trên PurchaseImportV2" },
  "logic_in_model-1a2b3c4d": { "verdict": "dismissed", "note": "scope thuần, không đọc setting" },
  "sql_calculation-5e6f7a8b": { "verdict": "needs author", "note": "SUM trong SQL, hỏi lý do" }
}
```

```bash
.claude/skills/pr-report/scripts/build-pr-report.sh --verdicts /tmp/verdicts.json
```

`verdict` là chuỗi tự do, quy ước dùng `confirmed` / `dismissed` / `needs author`.
Finding chưa có trong file verdicts hiện là **"chưa đọc"** — người review thấy ngay
phần nào máy nói mà chưa ai kiểm.

## Output

`pr-report.md` — để đọc và dán vào PR. `pr-report.json` — cùng nội dung, để bước khác ăn:

`meta` (branch, base, head_sha, nguồn + giờ sinh của 3 file vào) · `verdict` (`level`, `reasons`) ·
`warnings` (diff cũ / map lệch commit / thiếu bước) · `scope` · `blast` · `stats` ·
`read_order[]` · `findings{group: []}` · `limits`.

## Giới hạn

- Không đọc source, không chạy git. Sai ở 3 bước trước thì sai ở đây — báo cáo
  chỉ trình bày lại, không sửa và không thêm phát hiện mới.
- `map_possibly_stale` (bản đồ KR1 quét ở commit khác) chỉ được **cảnh báo**, không
  chặn. Số màn hình khi đó là ước lượng.
- Xếp loại rủi ro nói về **độ lan** và **số vi phạm quy chuẩn**, không nói code đúng hay sai.
  Một PR "rủi ro thấp" vẫn có thể sai logic.

## Related

| For | See |
|---|---|
| Đọc diff (step 1) | `.claude/skills/pr-diff/SKILL.md` |
| Đối chiếu bản đồ (step 2) | `.claude/skills/blast-radius/SKILL.md` |
| Phát hiện lỗi (step 3) | `.claude/skills/pr-check/SKILL.md` |
| Chạy cả 4 bước | `.claude/commands/review-pr.md` |
