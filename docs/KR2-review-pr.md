# KR2 — Review PR theo phạm vi ảnh hưởng (chi tiết)

> Tổng quan ở `../README.md`. **Mỗi Target của KR2 được skill nào làm và đã đạt tới đâu: xem `../README.md` mục 0 "Đối chiếu với mục tiêu KR".**
> File này giải thích từng bước **chạy bên trong ra sao**,
> đi qua **một ví dụ thật từ đầu tới cuối**, rồi giải thích **từng trường** của 4 file JSON.

---

## 0. Bức tranh chung

```
git diff ──► [1] pr-diff ──► pr-diff.json ─┐
                                          ├─► [2] blast-radius ──► blast-radius.json ─┐
bản đồ KR1 (impact.json) ─────────────────┘                                          │
                                                                                     ├─► [3] pr-check ──► pr-check.json ─┐
source code ─────────────────────────────────────────────────────────────────────────┘                                  │
                                                                                                                         ├─► [4] pr-report ──► pr-report.md
Claude đọc code, ghi kết luận (verdicts.json) ───────────────────────────────────────────────────────────────────────────┘
```

| Bước | Skill | Đọc source? | Chạy git? | Phán đúng sai? |
|---|---|---|---|---|
| 1 | `pr-diff` | có (để biết dòng nằm trong method nào) | có | không |
| 2 | `blast-radius` | không | không | không — chỉ đo độ lan |
| 3 | `pr-check` | có | có (so với bản ở base) | **đưa nghi vấn**, không kết luận |
| — | Claude | có | có | **kết luận** từng nghi vấn |
| 4 | `pr-report` | không | không | xếp loại theo luật cố định |

Máy chỉ gom bằng chứng. **Người (hoặc Claude) mới là bên kết luận.** Bước "Claude đọc code" nằm giữa bước 3 và 4 là cố ý.

---

## 1. Cách từng bước chạy bên trong

### 1.1 `pr-diff` — đọc phần code PR vừa sửa

1. Xác định hai đầu so sánh:
   - mặc định: `base` = điểm tách khỏi `origin/master` (merge-base), `head` = `HEAD`;
   - `--base <ref>`: so với mốc tự chọn (vd PR xếp chồng: so KAIZ-384 với đầu nhánh KAIZ-383);
   - `--working`: code chưa commit; `--staged`: đã `git add`; `--pr 1234`: theo số PR.
2. Lấy `git diff` và ghi lại từng khoảng dòng thêm / xoá.
3. Với file Ruby: đọc cả bản trước lẫn bản sau, tìm mỗi dòng sửa nằm trong `class` / `module` / `def` nào (dựa vào thụt lề, vì repo đã format bằng rubocop), và method nào là **mới hoàn toàn**.
4. Ghi tên method theo đúng cách KR1 gọi (`Api::InventoryVariantsController#available_variants`), để bước 2 tra ngược vào bản đồ được.

Hiện chỉ tách class/method cho Ruby. File TS / Vue / ngôn ngữ khác chỉ ghi ở mức "file nào, dòng nào".

### 1.2 `blast-radius` — đối chiếu với bản đồ KR1

1. Với mỗi file PR sửa, tra `impact.json` xem bao nhiêu màn hình chạm tới file đó (`shared_files`).
2. Xếp mức rủi ro cho **từng file** theo **tỉ lệ** màn hình chạm tới. Dùng tỉ lệ chứ không dùng số tuyệt đối, để repo lớn lên thì ngưỡng vẫn còn ý nghĩa:

   | Mức | Khi nào |
   |---|---|
   | `critical` | ≥ 30% số màn hình |
   | `high` | ≥ 5%, **hoặc** là file hub (≥ 40 file tham chiếu) |
   | `medium` | ≥ 1% |
   | `low` | có ít nhất 1 màn hình |
   | `none` | không màn hình nào |
   | `unknown` | bản đồ không biết file (spec, doc, config…) |

3. Mức của **cả PR** (`pr_risk`) = mức cao nhất trong các file.
4. **Màn hình bị viết lại**: màn hình mà chính controller action của nó nằm trong vùng sửa, khác với màn hình chỉ bị ảnh hưởng gián tiếp.
5. Kiểm bản đồ có cũ không: commit lúc quét bản đồ khác commit của PR thì ghi `map_possibly_stale: true`.

### 1.3 `pr-check` — phát hiện lỗi

**Nhóm "Phá chỗ khác"** (`checks_breaks.py`): so bản ở base với bản sau khi sửa, rồi tìm trong cả repo những chỗ còn dùng thứ đã bị đổi. Chỗ gọi nằm trong chính phần PR thêm vào được coi là đã được sửa theo.

| Loại | Khi nào | Mức |
|---|---|---|
| `removed_but_still_called` | xoá method Ruby / export TS mà nơi khác còn gọi | error |
| `signature_changed` | đổi tham số làm lời gọi cũ hỏng, chỗ gọi chưa sửa | error |
| `removed_class_still_used` | xoá / đổi tên class, module mà còn được dùng | error |
| `removed_action_still_routed` | xoá action controller mà route vẫn trỏ vào | error |
| `removed_i18n_key_still_used` | xoá key dịch mà `$t('…')` còn dùng | error |
| `shared_method_rewritten` | sửa method có sẵn trong file mức critical/high (từ bước 2) | warning |
| `untested_change` | method public mới trong model/service/job/form mà spec chưa có hoặc không nhắc tới | warning |

`evidence.match`: `exact` (tìm thấy dạng `Class.method`) thì là error; `name_only` (chỉ trùng tên, có thể là method của class khác) thì hạ xuống warning.

**Nhóm "Code lặp / thừa"** (`checks_duplicate.py`):

| Loại | Khi nào | Mức |
|---|---|---|
| `duplicate_method` | thân method mới giống hệt một method có sẵn | warning |
| `duplicate_code` | từ 6 dòng thêm vào giống hệt code ở chỗ khác | info |
| `unused_new_method` | method mới không ai gọi | warning |
| `commented_out_code` | từ 3 dòng code bị comment lại | warning |

**Nhóm "Sai quy chuẩn"** (`checks_rules.py`) — chỉ soi dòng PR thêm vào:

| Loại | Luật | Nguồn | Mức |
|---|---|---|---|
| `ruby33_incompatible` | cú pháp bị bỏ ở Ruby 3.2+ (`File.exists?`, `=~` với vế trái không phải String…) | `.claude/rules` | error |
| `debug_left` | còn `binding.pry` / `byebug` (Ruby — error), `debugger` / `console.log` (frontend — warning) | `.claude/rules` | error / warning |
| `focused_or_skipped_test` | test bị `fit` / `.only` / `skip` | `.claude/rules` | error |
| `ts_any` | dùng kiểu `any` | `.claude/rules` | error |
| `vue_v_html` | dùng `v-html` (nguy cơ XSS), phải dùng `z-safe-html` | `.claude/rules` | error |
| `packwerk` | thêm vi phạm vào `package_todo.yml` (giấu lỗi thay vì sửa), model bị đánh dấu `pack_public` | `.claude/rules` | error |
| `hardcoded_text` | chữ Nhật viết cứng thay vì dùng i18n | `.claude/rules` | warning |
| `gtm` | nút mới thiếu `data-gtm-id`, hoặc id sai định dạng | `.claude/rules` | warning / error |
| `commit_message_format` | commit không theo `feat: KEY nội dung #time 1h` | `.claude/rules` | warning |
| `spec_mock_stub` | spec dùng mock / stub thay vì dữ liệu thật (FactoryBot) | `CLAUDE.local.md` | error |
| `controller_business_logic` | controller chứa query / transaction / job, thay vì chỉ gọi Service rồi render | `CLAUDE.local.md` | warning |
| `logic_in_model` | method mới trong Model đọc setting / model khác / SQL | `CLAUDE.local.md` | warning |
| `service_calls_service` | Service gọi Service khác (phải dùng chung qua Concern) | `CLAUDE.local.md` | warning |
| `sql_calculation` | tính toán nghiệp vụ trong SQL (`SUM`, `+ -` trong chuỗi SQL) | `CLAUDE.local.md` | warning |
| `string_sql_condition` | điều kiện SQL viết bằng chuỗi thay vì DSL ActiveRecord | `CLAUDE.local.md` | warning |
| `query_in_loop` | query nằm trong vòng lặp | `CLAUDE.local.md` | warning |
| `loop_invariant_condition` | điều kiện không phụ thuộc phần tử nhưng đặt trong vòng lặp | `CLAUDE.local.md` | warning |

Bỏ qua một dòng cố ý viết như vậy: thêm comment `pr-check:ignore <lý do>`. Lý do là bắt buộc, vì đó là thứ người đọc sau sẽ thấy thay cho cảnh báo.

### 1.4 Claude xác nhận — bước không script nào làm được

Với **mỗi** `error` và `warning`, Claude mở đúng `file:dòng` và các chỗ gọi trong `evidence`, rồi chọn một trong ba:

| Kết luận | Nghĩa |
|---|---|
| `confirmed` | lỗi thật ở dòng đó |
| `dismissed` | báo nhầm — kèm một câu lý do |
| `needs author` | chỉ người viết mới biết có cố ý hay không |

Trước khi xác nhận, **phải kiểm dòng đó có đúng là do PR này thêm vào không** (`git show <base>:<file>`). Bẫy thường gặp: PR chỉ bọc một dòng có sẵn vào `if` / `elsif`, nên diff tính cả dòng là "mới", nhưng thực ra dòng đó đã có từ trước.

Những báo nhầm hay gặp khác: `name_only` trùng tên method của class khác; method được gọi qua `send` hoặc trong template; setup spec cố ý lặp lại.

`info` là để người quyết, không cần xác nhận từng cái.

Kết luận được ghi vào một file JSON (khoá là `id` của phát hiện), rồi dựng lại báo cáo bằng `--verdicts`. **Không sửa tay vào `pr-report.md`**, vì lần chạy sau sẽ ghi đè.

### 1.5 `pr-report` — tạo báo cáo

Xếp loại cả PR theo luật cố định, không có điểm ẩn:

| Mức | Khi nào |
|---|---|
| **Cao** | có `error`, **hoặc** đụng file dùng chung mức critical/high **và** có `warning` |
| **Vừa** | đụng file dùng chung mức critical/high, **hoặc** có `warning` |
| **Thấp** | không có cả hai |
| **Chưa kết luận** | chưa chạy bước 3 — chưa rà thì không được nói là an toàn |

Số màn hình bị viết lại luôn được nêu trong phần lý do nhưng **không tự nâng mức**: viết lại một màn hình ít người dùng không nguy hiểm bằng sửa một dòng trong file mà 800 màn hình chạm tới.

Bảng "Đọc theo thứ tự này" sắp theo: số `error` giảm dần → mức rủi ro của file → số màn hình chạm tới → số `warning`. File có lỗi mà bản đồ không biết (spec…) vẫn nằm trong bảng.

---

## 2. Ví dụ trọn quy trình: nhánh KAIZ-384

Chạy `/review-pr` trên nhánh `KAIZ-384`, so với master (`225b806fa` → `20626c5b8`, gồm cả 382 + 383 + 384). Bản đồ KR1 đã quét lại ở đúng commit này trước khi chạy, nên số màn hình là số thật.

**Bước 1 — pr-diff:** 73 file, +12.852 / −107 dòng, 80 class/method bị đụng.

**Bước 2 — blast-radius:** 954 / 1.498 màn hình (64%) có thể bị ảnh hưởng, mức PR **critical**.

```
critical  843  app/models/company.rb                                  [hub]
high      394  app/models/general_setting.rb
high      298  app/models/analysis.rb                                 [hub]
high      277  app/models/inventory_variant.rb
medium     30  app/controllers/packing_slips_controller.rb
…
Màn hình bị viết lại hẳn (7): api/inventory_variants#available_variants, #suggest_assign,
packing_slips#new / #show / #edit, function_preferences#edit / #update
```

**Bước 3 — pr-check:** 30 phát hiện: 0 error, 4 warning, 26 info.

**Claude xác nhận 4 warning:**

| Phát hiện | Kết luận | Lý do |
|---|---|---|
| `logic_in_model` · `company.rb:440` | **confirmed** | `allocate_variant_by_assignable_quantity?` đọc `GeneralSetting` ngay trong Model. File đã có sẵn một chỗ cùng kiểu, nên đây là lệ cũ, không phải PR này mở đầu |
| `sql_calculation` · `inventory_assignable_quantity_calculable.rb:37` | **needs author** | `group(:data_key).sum(:quantity)`: chuyển sang Ruby thì phải nạp hết `purchase_item_variants` |
| `sql_calculation` · `…:47` | **needs author** | cùng dạng, trên `delivery_variants` |
| `string_sql_condition` · `suggest_assign_inventory_variant_service.rb:94` | **dismissed** | `where('quantity > 0')` đã có ở base (dòng 71); PR chỉ bọc vào nhánh `elsif` |

26 `info` đều là setup spec / test TS lặp lại, để người quyết.

**Bước 4 — pr-report:**

```
RỦI RO CAO — cần senior đọc kỹ trước khi merge
- đụng file dùng chung mức critical và có 4 cảnh báo
- viết lại hẳn 7 màn hình
```

**Đối chứng:** cùng quy trình với riêng PR 384 (so với đầu nhánh 383, chỉ sửa spec) → 9 file, 0 màn hình, 0 error / 0 warning / 2 info → **RỦI RO THẤP**. Với KAIZ-381 → 1 error, 13 warning → **RỦI RO CAO**. Sang stack 382–384 thì error về 0 và warning tụt 13 → 4, vì phần SQL đã được chuyển từ Model sang concern và viết lại bằng DSL của ActiveRecord. Công cụ đo được cả việc code đã được dọn.

---

## 3. Từng trường trong các file JSON

> Ví dụ trong phần này lấy từ lần chạy trên **riêng PR KAIZ-384** (base = đầu nhánh KAIZ-383
> `af74d33f6b`, head `20626c5b88`). Chỗ nào PR đó không có dữ liệu thì ví dụ lấy từ KAIZ-381, có ghi chú.

### 3.1 `pr-diff.json` — PR sửa dòng nào

#### `meta`

```json
{
 "generated_at": "2026-09-24T01:13:01Z",
 "tool_version": "3.0.0",
 "root": "/Users/thuong.pham/Documents/TMS/zaico_web",
 "mode": "branch",
 "base": "af74d33f6bbc82e3fbb83715bf0b4c9230132173",
 "head": "HEAD",
 "head_sha": "20626c5b88e93dd4d1be0acdbc357116809c84ab",
 "branch": "KAIZ-384",
 "symbol_langs": ["ruby"],
 "pr": null
}
```

| Trường | Nghĩa |
|---|---|
| `generated_at` | Giờ chạy (UTC). Bước sau so giờ này để biết dữ liệu có cũ không |
| `tool_version` | Phiên bản script. Bước sau từ chối chạy nếu số đầu không khớp — tránh đọc file cũ theo cấu trúc mới |
| `root` | Thư mục repo lúc chạy |
| `mode` | So cái gì với cái gì: `branch` (nhánh vs base), `working` (code chưa commit), `staged` (đã `git add`), `pr` (số PR) |
| `base` | Mốc để so. Mặc định là merge-base với `origin/master`; ở đây truyền tay `af74d33f6b` để chỉ lấy đúng phần của KAIZ-384, không lẫn 382/383 |
| `head` | Đầu bên kia, thường là `HEAD` |
| `head_sha` | Commit thật của head. Bước sau dùng cái này để biết code có bị đổi giữa chừng không |
| `branch` | Tên nhánh. Detached HEAD thì ra chữ `HEAD` |
| `symbol_langs` | Ngôn ngữ mà script đọc được tới mức class/method. Hiện chỉ Ruby; TS/Vue chỉ ghi theo file |
| `pr` | Số PR nếu chạy bằng `--pr 1234`, không thì `null` |

#### `stats`

```json
{"files_changed": 9, "files_added": 3, "files_modified": 6,
 "lines_added": 999, "lines_removed": 6, "symbols_touched": 12}
```

| Trường | Nghĩa |
|---|---|
| `files_changed` | Tổng số file đụng tới |
| `files_added` / `files_modified` | Trong đó bao nhiêu file mới, bao nhiêu file sửa |
| `lines_added` / `lines_removed` | Số dòng thêm / xoá |
| `symbols_touched` | Số class / method bị đụng. KAIZ-384 là 12 |

#### `files[]` — mỗi file một phần tử

```json
{
 "path": "spec/services/packing_slips/bulk_auto_allocate_variants_service_spec.rb",
 "old_path": null,
 "status": "modified",
 "binary": false,
 "added":   [{"from": 62, "to": 63}, {"from": 75, "to": 90}, {"from": 687, "to": 743}],
 "removed": [{"from": 62, "to": 62}, {"from": 73, "to": 75}],
 "lang": "ruby",
 "symbols": {
   "def":     ["create_stock_variant", "build_delivery"],
   "new_def": ["create_variant_inventory", "allocate_with"]
 }
}
```

| Trường | Nghĩa |
|---|---|
| `path` | Đường dẫn hiện tại |
| `old_path` | Tên cũ nếu file bị đổi tên, không thì `null` |
| `status` | `added` / `modified` / `deleted` / `renamed` |
| `binary` | File nhị phân (ảnh…) thì `true`, không đọc nội dung |
| `added[]` | **Các khoảng dòng PR thêm vào, tính theo file mới.** `{"from":75,"to":90}` = dòng 75 đến 90 (tính cả hai đầu) |
| `removed[]` | Các khoảng dòng bị xoá, tính theo file cũ |
| `lang` | Đoán theo đuôi file: `ruby`, `vue`, `ts`, `markdown`… |
| `symbols.class` | Class/module chứa vùng sửa |
| `symbols.def` | Method **đã có từ trước** mà PR sửa vào trong |
| `symbols.new_def` | Method **mới hoàn toàn** PR thêm vào |

**`def` và `new_def` khác nhau ở chỗ nào quan trọng:** `def` = sửa vào thứ người
khác đang gọi → bước 3 đi tìm chỗ gọi xem có gãy không. `new_def` = thứ mới →
bước 3 kiểm ngược lại: có ai gọi không, có test chưa.

> `added` chỉ biết "dòng này mới trong diff", **không** biết "logic này mới".
> PR bọc một dòng cũ vào `if` thì cả dòng bị tính là mới. Đây là nguồn báo nhầm
> chính ở bước 3, và là lý do phải có người đọc xác nhận lại.

---

### 3.2 `blast-radius.json` — sửa vậy đụng tới đâu

Lấy danh sách file ở bước 1 tra vào bản đồ KR1 (`impact.json`). Không đọc code, không chạy git.

#### `meta`

```json
{
 "diff_source": ".ai/code-map/raw/pr-diff.json",
 "impact_source": ".ai/code-map/analysis/impact.json",
 "diff_base": "af74d33f6bbc...",
 "diff_head_sha": "20626c5b88e9...",
 "branch": "KAIZ-384",
 "map_scan_commit": "e2fb4e8f4efe...",
 "map_generated_at": "2026-09-17T07:01:45Z",
 "map_max_depth": 3,
 "map_possibly_stale": true
}
```

| Trường | Nghĩa |
|---|---|
| `diff_source` / `impact_source` | Hai file đầu vào |
| `map_scan_commit` | Bản đồ KR1 được quét ở commit nào |
| `map_generated_at` | Bản đồ dựng lúc nào |
| `map_max_depth` | Bản đồ đi sâu mấy tầng từ màn hình xuống file (mặc định 3) |
| `map_possibly_stale` | **`true` = bản đồ quét ở commit khác với PR này**, số màn hình chỉ là ước lượng. Ở đây `true` vì bản đồ quét ở `e2fb4e8` (nhánh KAIZ-381) còn PR đang ở `20626c5b`. Chạy `/map --refresh` là hết |

#### `stats`

```json
{"files_changed": 9, "files_mapped": 0, "files_unused": 0, "files_new": 0,
 "files_check_manually": 0, "files_not_scanned": 9, "files_non_code": 3,
 "hubs_touched": 0, "screens_at_risk": 0, "screens_rewritten": 0,
 "screens_total": 1498, "screen_risk_ratio": 0.0,
 "packs_at_risk": [], "file_risks": {}, "pr_risk": "none"}
```

| Trường | Nghĩa |
|---|---|
| `files_mapped` | File có trong bản đồ → biết được đụng màn hình nào |
| `files_unused` | File có trong bản đồ nhưng **không màn hình nào chạm tới** — nghi là code chết |
| `files_new` | File mới tinh, bản đồ chưa biết là đương nhiên |
| `files_check_manually` | **File cũ mà bản đồ không biết** → hoặc bản đồ cũ, hoặc code gọi kiểu động. Phải tự xem |
| `files_not_scanned` | File nằm ngoài vùng KR1 quét (`spec/`, `doc/`, `db/`, `.github/`, `bin/`…). KAIZ-384 toàn spec và doc nên cả 9 file vào đây — **đúng, không phải lỗi** |
| `files_non_code` | File `.md .txt .json .yml .lock`. Không loại bỏ, chỉ đánh dấu để bước 3 khỏi bận tâm |
| `hubs_touched` | Số file dùng chung cả hệ thống bị đụng |
| `screens_at_risk` | Số màn hình có đường đi tới ít nhất một file vừa sửa |
| `screens_rewritten` | Số màn hình mà **chính controller action của nó** bị sửa — không phải ảnh hưởng gián tiếp |
| `screens_total` | Tổng số màn hình trong bản đồ (1498) |
| `screen_risk_ratio` | `screens_at_risk / screens_total`. `0.0` ở đây, KAIZ-381 là `0.6155` |
| `packs_at_risk` | Các pack Packwerk bị dính |
| `file_risks` | Đếm file theo mức, kiểu `{"critical":1,"high":2,"medium":3,"low":4}` |
| `pr_risk` | Mức cao nhất trong đám file. `none` = không đụng file nào có màn hình dùng |

#### `files[]`

Ví dụ KAIZ-384 (file tài liệu, ngoài vùng quét):

```json
{"path": "doc/issues/KAIZ-384/plan.md", "status": "added", "lang": "markdown",
 "lines_added": 223, "lines_removed": 0, "non_code": true,
 "map_status": "not_scanned", "screens_reached": null, "hub": false, "risk": "unknown"}
```

Ví dụ file thật có màn hình dùng (lấy từ lần chạy KAIZ-381):

```json
{"path": "app/models/company.rb", "status": "modified", "lang": "ruby",
 "lines_added": 12, "lines_removed": 0,
 "symbols": {"class": ["Company"], "new_def": ["Company#allocate_variant_by_assignable_quantity?"]},
 "non_code": false, "map_status": "mapped", "layer": "models",
 "screens_reached": 843, "screens_reached_ratio": 0.5628,
 "hub": true, "referenced_by_files": 172, "risk": "critical"}
```

| Trường | Nghĩa |
|---|---|
| `lookup_path` | Đường dẫn dùng để tra bản đồ (file đổi tên thì tra tên cũ) |
| `map_status` | `mapped` (bản đồ biết) · `unused` (bản đồ biết nhưng không ai dùng) · `new_file` · `check_manually` · `not_scanned` |
| `layer` | Tầng: `controllers` / `models` / `services` / `views` / `jobs`… |
| `screens_reached` | **Bao nhiêu màn hình có đường đi tới file này.** `company.rb` = 843 |
| `screens_reached_ratio` | Tỉ lệ trên tổng 1498 màn hình |
| `hub` | `true` khi **từ 40 file trở lên** tham chiếu tới nó. Đây là fan-in, khác với `screens_reached` |
| `referenced_by_files` | Con số fan-in thật (`company.rb` = 172 file) |
| `risk` | Mức rủi ro của riêng file này |

**Luật tính `risk` — chỉ có bấy nhiêu:**

| Mức | Khi nào |
|---|---|
| `critical` | chạm tới **≥ 30%** số màn hình |
| `high` | ≥ 5%, **hoặc** là hub (≥ 40 file tham chiếu) |
| `medium` | ≥ 1% |
| `low` | có ít nhất 1 màn hình |
| `none` | không màn hình nào |
| `unknown` | bản đồ không biết file này |

Dùng **tỉ lệ** chứ không dùng số tuyệt đối, để repo to lên thì ngưỡng vẫn còn nghĩa.

#### `screens[]`

Mỗi màn hình bị ảnh hưởng một phần tử. KAIZ-384 rỗng (không đụng file app nào).
Ví dụ từ KAIZ-381:

```json
{"screen": "api/inventory_variants#available_variants",
 "rewritten": true,
 "rewritten_methods": ["Api::InventoryVariantsController#available_variants"],
 "route": "GET /(/companies/:company_id)/api/inventory_variants/available_variants",
 "changed_files": ["app/controllers/api/inventory_variants_controller.rb",
                   "app/models/company.rb", "app/models/inventory_variant.rb"]}
```

| Trường | Nghĩa |
|---|---|
| `screen` | `controller#action` |
| `rewritten` | `true` = chính action này bị sửa, không phải ảnh hưởng gián tiếp |
| `rewritten_methods` | Method cụ thể bị sửa |
| `route` | URL tới màn hình — để QA biết bấm chỗ nào mà thử |
| `changed_files` | Các file PR sửa mà màn hình này chạm tới |

> **Màn hình không bị viết lại chỉ có 2 trường `screen` và `changed_files`** — ba trường
> `rewritten` / `rewritten_methods` / `route` bị lược hẳn cho nhẹ file. Đọc bằng code thì
> dùng `s.get('rewritten')` chứ đừng `s['rewritten']`, không là nổ `KeyError`.

---

### 3.3 `pr-check.json` — có sai gì không

Đây là bước duy nhất **có đọc source**. Chỉ soi phần PR thêm vào (trừ nhóm `breaks`,
nhóm này phải quét cả repo tìm chỗ gọi).

#### `meta`

```json
{"generated_at": "2026-09-24T01:13:09Z", "tool_version": "2.0.0",
 "diff_head_sha": "20626c5b88e9...", "branch": "KAIZ-384",
 "blast_radius_used": true, "diff_possibly_stale": false,
 "files_checked": 9, "seconds": 7.4}
```

| Trường | Nghĩa |
|---|---|
| `blast_radius_used` | Có dùng kết quả bước 2 để xếp thứ tự phát hiện không |
| `diff_possibly_stale` | `true` = HEAD đã đổi sau khi chạy bước 1, số liệu có thể lệch → chạy lại từ đầu |
| `files_checked` | Số file thật sự đem soi |
| `seconds` | Thời gian chạy |

#### `stats`

```json
{"findings": 2, "errors": 0, "warnings": 0, "infos": 2,
 "by_group": {"duplicate": 2}, "by_check": {"duplicate_code": 2}}
```

| Trường | Nghĩa |
|---|---|
| `findings` | Tổng số phát hiện |
| `errors` / `warnings` / `infos` | Chia theo mức nghiêm trọng, cộng lại bằng `findings` |
| `by_group` | Chia theo nhóm: `breaks` / `duplicate` / `rules` |
| `by_check` | Chia theo từng loại kiểm tra cụ thể |

Đối chiếu KAIZ-381 để thấy khác biệt:
`{"findings":20,"errors":1,"warnings":13,"infos":6,"by_group":{"rules":14,"duplicate":6}}`
— PR đó có sửa code app nên dính 14 lỗi quy chuẩn; KAIZ-384 toàn spec nên chỉ còn 2 chỗ spec lặp.

**Ba mức:**

| Mức | Nghĩa |
|---|---|
| `error` | Gãy chỗ khác, hoặc vi phạm luật cấm hẳn. Không nên merge trước khi xử |
| `warning` | Nhiều khả năng sai, phải đọc mới biết |
| `info` | Để người quyết, không phải lỗi |

#### `findings[]`

```json
{
 "id": "duplicate_code-20adbb14",
 "group": "duplicate",
 "check": "duplicate_code",
 "severity": "info",
 "file": "spec/models/packing_slip_import_v2_spec.rb",
 "line": 860,
 "message": {
   "vi": "Dòng 860-867 giống hệt spec/models/packing_slip_import_v2_spec.rb:759-766.",
   "en": "Lines 860-867 duplicate spec/models/packing_slip_import_v2_spec.rb:759-766."
 },
 "evidence": {
   "duplicate_of": {"file": "spec/models/packing_slip_import_v2_spec.rb", "from": 759, "to": 766},
   "lines": 8
 }
}
```

| Trường | Nghĩa |
|---|---|
| `id` | `<loại>-<hash>`. Ổn định giữa các lần chạy nếu code không đổi → dùng làm khoá khi ghi kết luận ở bước 4 |
| `group` | `breaks` (phá chỗ khác) / `duplicate` (lặp, chết) / `rules` (sai quy chuẩn) |
| `check` | Loại cụ thể: `removed_but_still_called`, `duplicate_code`, `logic_in_model`… |
| `severity` | `error` / `warning` / `info` |
| `file`, `line` | Chỗ bị chỉ. `line` là `null` khi thứ bị chỉ đã bị xoá mất |
| `message.vi` / `.en` | Một câu giải thích, hai thứ tiếng |
| `rule` | Luật nào bị vi phạm (`CLAUDE.local.md`, `.claude/rules/...`). Nhóm `duplicate` không có trường này |
| `evidence` | Bằng chứng, tuỳ loại mà khác nhau |

**`evidence` có gì, theo từng loại:**

| Loại phát hiện | `evidence` chứa |
|---|---|
| `duplicate_code` | `duplicate_of` (file + khoảng dòng giống) và `lines` (dài bao nhiêu dòng) |
| `removed_but_still_called` | `call_sites` — danh sách `file:dòng` còn đang gọi, kèm `match` là `exact` (chắc chắn, ra `error`) hay `name_only` (chỉ trùng tên, có thể của class khác, ra `warning`) |
| `signature_changed` | `before` / `after` — chữ ký cũ và mới |
| nhóm `rules` | `code` — đúng dòng code bị chỉ |

Ví dụ từ KAIZ-381:

```json
{"id": "spec_mock_stub-969b6936", "group": "rules", "check": "spec_mock_stub",
 "severity": "error", "file": "spec/models/purchase_import_v2_spec.rb", "line": 532,
 "message": {"vi": "Spec dùng mock/stub; dựng trạng thái bằng record thật (FactoryBot)."},
 "rule": "CLAUDE.local.md",
 "evidence": {"code": "allow_any_instance_of(PurchaseImportV2).to receive(:read_csv_file).and_return(csv_data)"}}
```

> Script **không** kết luận. Nó chỉ đưa nghi vấn kèm bằng chứng. Sau đó phải mở
> đúng `file:line` đọc mới biết thật hay nhầm — ví dụ trên KAIZ-381 có một cảnh báo
> hoá ra là dòng đã tồn tại từ trước, PR chỉ bọc nó vào `elsif`.

---

### 3.4 `pr-report.json` / `pr-report.md` — gộp lại, xếp loại

Gộp 3 file trên. Không đọc code, không chạy git, không tự thêm phát hiện mới.
File `.md` là để người đọc; file `.json` cùng nội dung ở dạng máy đọc được
(hiện chưa có gì dùng tới, để dành sau này cho bot hoặc CI).

#### `meta`

```json
{"generated_at": "2026-09-24T01:13:09Z", "tool_version": "1.0.0",
 "branch": "KAIZ-384", "base": "af74d33f6bbc...", "head_sha": "20626c5b88e9...",
 "pr": null, "lang": "vi",
 "sources": {"pr_diff": "2026-09-24T01:13:01Z",
             "blast_radius": "2026-09-24T01:13:01Z",
             "pr_check": "2026-09-24T01:13:09Z"}}
```

`sources` ghi giờ sinh của cả 3 file đầu vào — nhìn là biết báo cáo dựng từ dữ liệu
lúc nào, có file nào cũ hơn hẳn không.

#### `verdict` — phần kết luận

```json
{"level": "low", "reasons": ["không có lỗi, không đụng file dùng chung"]}
```

KAIZ-381 thì ra:
```json
{"level": "high", "reasons": ["có 1 lỗi chặn (error)", "viết lại hẳn 3 màn hình"]}
```

| `level` | Khi nào |
|---|---|
| `high` | có `error`, **hoặc** file dùng chung `critical`/`high` mà còn có `warning` |
| `medium` | file dùng chung `critical`/`high`, **hoặc** có `warning` |
| `low` | không dính cả hai |
| `unknown` | **chưa chạy bước 3** — chưa rà thì không được nói là an toàn |

`reasons[]` liệt kê đúng những điều kiện đã bật. Số màn hình bị viết lại luôn được
nêu nhưng **không tự nâng mức**: viết lại một màn hình ít người dùng không nguy hiểm
bằng sửa một dòng trong file mà 800 màn hình đụng tới.

#### `warnings[]`

Cảnh báo về chất lượng dữ liệu, in ngay đầu bản `.md`:

- `"Bản đồ KR1 quét ở commit khác với PR này..."` — từ `map_possibly_stale`
- `"Diff có thể cũ: HEAD đã đổi sau khi chạy step 1."` — từ `diff_possibly_stale`
- `"Chưa chạy blast-radius / pr-check..."` — thiếu bước

#### `scope`, `blast`, `stats`

Chép lại số liệu 3 bước trước, không tính toán gì thêm:
`scope` từ bước 1, `blast` từ bước 2, `stats` từ bước 3.

#### `read_order[]` — đọc file nào trước

```json
{"file": "spec/models/inventory_variant_spec.rb", "risk": "unknown", "layer": null,
 "screens_reached": null, "referenced_by_files": null,
 "lines_added": 42, "lines_removed": 0,
 "errors": 0, "warnings": 0, "infos": 0}
```

Mỗi file một dòng, kèm số lỗi của riêng nó. **Thứ tự sắp**: nhiều `error` nhất lên
đầu → mức `risk` cao hơn → nhiều màn hình dùng hơn → nhiều `warning` hơn → cuối cùng
xếp theo tên cho ổn định.

File có lỗi nhưng bản đồ không biết (spec chẳng hạn) vẫn nằm trong bảng, không rơi mất.

#### `findings{}` — phát hiện, gom theo nhóm

Giống `findings[]` của bước 3 nhưng gom thành `{"rules": [...], "duplicate": [...]}`,
nhóm nào có lỗi nặng nhất thì đứng trước. Mỗi phát hiện có thêm 2 trường:

| Trường | Nghĩa |
|---|---|
| `verdict` | Kết luận sau khi người/Claude mở code đọc: `confirmed` / `dismissed` / `needs author` |
| `note` | Một câu lý do |

Hai trường này **không phải script tự điền**. Đọc code xong ghi ra một file JSON rồi
dựng lại báo cáo:

```json
{
  "spec_mock_stub-969b6936": {
    "verdict": "confirmed",
    "note": "allow_any_instance_of trên PurchaseImportV2"
  },
  "string_sql_condition-725a3fbe": {
    "verdict": "dismissed",
    "note": "dòng where('quantity > 0') đã có ở base line 71, PR chỉ bọc vào elsif"
  }
}
```

```bash
.claude/skills/pr-report/scripts/build-pr-report.sh --verdicts /tmp/verdicts.json
```

Khoá chính là `id` ở bước 3. Phát hiện nào chưa có trong file này thì bản `.md` ghi
là **"chưa đọc"** — nhìn ra ngay phần nào máy nói mà chưa ai xác minh.

Đừng sửa tay vào `pr-report.md`, lần chạy sau ghi đè.

#### `limits[]`

4 câu giới hạn, luôn in ở cuối báo cáo:

- blast-radius đếm màn hình bằng tham chiếu tên, không chạy code → gọi động bị bỏ sót
- pr-check là grep + so base/head, không phải type system → **không có phát hiện ≠ an toàn**
- không chạy lint / test / type-check — việc của CI
- lỗi logic (`>` thành `>=`) ngoài tầm mọi bước máy, phải người đọc

---

## 4. Giới hạn

- **Không có phát hiện không có nghĩa là an toàn.** Chỗ gọi được tìm bằng tên, không phải bằng hệ thống kiểu: `send`, `constantize`, key dịch ghép động sẽ không thấy.
- **Không chạy** rubocop, eslint, test, type-check — đó là việc của CI.
- **Lỗi logic** (`>` thành `>=`) nằm ngoài tầm mọi bước máy; phải người hoặc Claude đọc.
- **Số màn hình** chỉ chính xác khi bản đồ KR1 quét ở cùng commit với PR (`map_possibly_stale: false`). Nếu không, chạy `/map --refresh` trước.
- **Mức rủi ro nói về độ lan và số vi phạm quy chuẩn**, không nói code đúng hay sai. PR "rủi ro thấp" vẫn có thể sai logic.
- `pr-diff` chỉ tách class/method cho Ruby; ngôn ngữ khác chỉ ghi ở mức file.
- Nhóm "Sai quy chuẩn" là luật riêng của zaico (`.claude/rules`, `CLAUDE.local.md`); team khác phải viết lại `checks_rules.py`.
