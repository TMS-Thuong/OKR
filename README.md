# Bộ skill KR1 / KR2 — Bản đồ code & Review PR theo phạm vi ảnh hưởng

Bộ skill cho Claude Code, làm cho repo **zaico_web** (Rails + Vue).

| KR | Tên | Trả lời câu hỏi |
|---|---|---|
| **KR1** | Bản đồ code | Mỗi màn hình dùng tới những file nào? File nào nhiều màn hình dùng chung? Sửa file X thì những màn hình nào bị ảnh hưởng? |
| **KR2** | Review PR theo phạm vi ảnh hưởng | PR này sửa gì? Đụng tới bao nhiêu màn hình? Có phá chỗ khác, có code lặp, có sai quy chuẩn không? PR rủi ro cao hay thấp? |

KR2 đứng trên KR1: muốn biết PR "đụng bao nhiêu màn hình" thì phải có bản đồ KR1 trước.

**Tài liệu chi tiết** (thư mục `docs/`):

| File | Nội dung |
|---|---|
| `docs/KR1-ban-do-code.md` | KR1 chạy bên trong ra sao, từng trường trong `scan.json` / `impact.json`, cách đặt tên tiếng Việt, cách đọc bản đồ |
| `docs/KR2-review-pr.md` | KR2 chạy bên trong ra sao, toàn bộ các loại kiểm tra, ví dụ trọn quy trình trên KAIZ-384, từng trường của 4 file JSON |

---

## 0. Đối chiếu với mục tiêu KR

Mỗi dòng *Target* trong bảng KR được làm bởi skill nào, và đã đạt tới đâu. Số liệu lấy từ các lần chạy thật trên zaico_web (commit `20626c5b`, nhánh KAIZ-384) và trên 8 app RealWorld.

### KR1 — Xây dựng Bản đồ ảnh hưởng: liên kết Module/Màn hình ↔ Logic ↔ File code

| Target | Skill | Làm bằng cách nào | Đã đạt |
|---|---|---|---|
| **Skill đọc code**: đọc file routes, model, controller trong hệ thống để lấy thông tin | `code-map` | Đọc DSL route của Rails, controller (action, `before_action`), model (association, scope), service, job, view, trang Vue, Packwerk pack; ghi thành `scan.json`. Repo không phải Rails thì có bộ đọc chung | Zaico: **1.572 route, 484 controller, 347 model, 747 service, 972 view, 1.263 trang Vue** trong ~6 giây. Ngoài Rails: 8 framework (Laravel, Express, NestJS, Spring, Django, FastAPI, Go/Gin, ASP.NET) đều tìm đủ 19/19 API, đúng hàm xử lý |
| **Skill phân tích / đối chiếu**: so sánh các file với nhau để tìm cái nào liên kết với cái nào, cái nào dùng chung nhiều chỗ | `impact-analysis` | Nối route → controller → service → model theo tham chiếu (tối đa 3 tầng); tìm file hub; tính file nào bị bao nhiêu màn hình dùng; tính màn hình nào liên quan màn hình nào | **1.498 màn hình**, trung bình **30,7 file / màn hình**; **22 file hub** (vd `company.rb`: 172 file tham chiếu, 843 màn hình chạm tới); 1.465 màn hình có danh sách màn hình liên quan. Tìm ra thêm **14 route trỏ vào controller không tồn tại** và 433 file không màn hình nào dùng |
| **Skill tạo tài liệu**: tổng hợp kết quả thành 1 bản đồ (file dữ liệu + file mô tả cho người đọc) | `code-map-doc` | File dữ liệu: `impact.json`. File cho người đọc: `index.html` (bản đồ dạng cây, tìm kiếm, VI/EN) và `summary.md` (bản chữ) | Bản đồ 1,85 MB mở thẳng bằng trình duyệt, không cần cài gì; **13 nhóm tính năng**; **80%** màn hình có tên tiếng Việt lấy thẳng từ từ điển (1.194 / 1.498) |

**=> Mục đích** *"biết chính xác mỗi chức năng/màn hình đang dùng tới những file code nào, chỗ nào dùng chung nhiều nơi — làm cơ sở cho KR2 và KR3"*:

- **Mỗi màn hình dùng file nào:** có ở hai mức: `code_files` (phạm vi cả controller, dùng để tính ảnh hưởng) và `flow` (cây gọi của riêng action, dùng để đọc hiểu).
- **Chỗ dùng chung:** `shared_files` và `hubs`, kèm bảng "25 file rủi ro nhất khi sửa" trong `summary.md`.
- **Cơ sở cho KR2:** đã dùng. Bước `blast-radius` của KR2 đọc thẳng `impact.json`.
- **Cơ sở cho KR3:** dữ liệu đã sẵn (mỗi màn hình → danh sách file, mỗi file → danh sách màn hình), đủ để chọn test theo phạm vi. **KR3 chưa làm.**

### KR2 — Command review PR theo phạm vi ảnh hưởng: phát hiện conflict logic, vi phạm DRY, chuẩn & tối ưu Rails

Ba nội dung trong tên KR ứng với ba nhóm kiểm tra của `pr-check`:

| Trong tên KR | Nhóm kiểm tra | Bắt những gì |
|---|---|---|
| **Conflict logic** | Phá chỗ khác (7 loại) | xoá / đổi tên method, class, action, key dịch mà nơi khác còn dùng; đổi tham số mà chỗ gọi chưa sửa; sửa method trong file nhiều màn hình dùng chung; method mới chưa có test |
| **Vi phạm DRY** | Code lặp / thừa (4 loại) | method giống hệt method có sẵn; từ 6 dòng chép y nguyên; method mới không ai gọi; code bị comment lại |
| **Chuẩn & tối ưu Rails** | Sai quy chuẩn (17 loại) | *tối ưu*: query trong vòng lặp, điều kiện cố định đặt trong vòng lặp, tính toán trong SQL. *chuẩn*: controller chỉ gọi Service, không để logic nghiệp vụ trong Model, Service không gọi Service, cấm mock trong spec, tương thích Ruby 3.3, Packwerk, i18n, GTM, format commit |

| Target | Skill | Làm bằng cách nào | Đã đạt |
|---|---|---|---|
| **Skill đọc code**: đọc phần code vừa sửa trong PR | `pr-diff` | Đọc `git diff`, ghi file / khoảng dòng / class / method bị sửa, method nào mới hoàn toàn | Stack KAIZ-382→384: **73 file, +12.852 dòng, 80 method** trong ~2 giây |
| **Skill đối chiếu dữ liệu**: so phần code sửa với bản đồ KR1 để biết có đụng chỗ quan trọng không | `blast-radius` | Tra từng file sửa vào `impact.json`: bao nhiêu màn hình chạm tới, có phải file dùng chung không; xếp mức critical / high / medium / low | Cùng PR: **954 / 1.498 màn hình (64%)** bị ảnh hưởng, chỉ ra đúng `company.rb` (843 màn hình, hub) là chỗ quan trọng nhất; **7 màn hình bị sửa thẳng** |
| **Skill kiểm tra / phát hiện lỗi**: rà xem có phá logic chỗ khác, có code lặp thừa, có sai quy chuẩn không | `pr-check` | 28 loại kiểm tra ở 3 nhóm trên; mỗi phát hiện có `file:dòng` và bằng chứng; sau đó **Claude mở code xác nhận từng cái** | KAIZ-381: **1 error, 13 warning** → sau khi đọc code: 9 lỗi thật, 4 cần hỏi tác giả, **1 báo nhầm được loại ra**. Stack 382–384: 4 warning, đã xác nhận cả 4 |
| **Skill tạo báo cáo**: gộp hết phát hiện thành 1 báo cáo, xếp loại PR rủi ro cao/thấp | `pr-report` | Luật xếp loại cố định (Cao / Vừa / Thấp / Chưa kết luận), bảng "đọc file nào trước", cột kết luận của Claude | KAIZ-381 → **Cao**; stack 382–384 → **Cao**; riêng PR 384 (chỉ sửa spec) → **Thấp** |

**=> Mục đích:**

| Mục đích | Làm được gì | Chưa đo / chưa làm |
|---|---|---|
| **Phát hiện vấn đề tiềm ẩn so với review của senior** | Bắt được loại lỗi senior hay chỉ ra: logic nghiệp vụ nằm trong Model, tính toán trong SQL, mock trong spec, method có sẵn bị sửa trong file cả nghìn màn hình dùng. Mỗi phát hiện đều được đọc lại, nên báo nhầm bị loại trước khi tới tay người review | **Chưa so với review thật của senior.** Cách đo: chạy trên các PR đã merge, đếm bao nhiêu comment review trên GitHub mà công cụ cũng bắt được, và ngược lại |
| **Quy trình review phân tầng theo rủi ro** | Mức Cao / Vừa / Thấp theo luật cố định; bảng "đọc file nào trước". Ví dụ: PR chỉ sửa spec ra **Thấp**, review thường là đủ; PR đụng `company.rb` ra **Cao**, cần senior đọc kỹ | Chưa có quy định của team về mỗi mức thì ai review, review kỹ tới đâu |
| **Giảm bug trong quá trình review và sửa code** | Bắt lỗi "phá chỗ khác" (xoá method còn được gọi, đổi tham số mà chỗ gọi chưa sửa) trước khi merge; chạy được cả trên code chưa commit (`--working`) | **Chưa đo số bug giảm được.** Cần theo dõi qua một khoảng thời gian: số bug sau merge ở các PR có chạy `/review-pr` so với không chạy |

---

## 1. Cấu trúc thư mục

```
Skill/
├── README.md                         ← file này
└── .claude/                          ← chép nguyên thư mục này vào repo để dùng
    ├── code-map.json                 cấu hình riêng của từng repo (từ điển tên tiếng Việt, nhóm tính năng…)
    ├── commands/
    │   ├── map.md                    lệnh /map        → chạy cả KR1
    │   └── review-pr.md              lệnh /review-pr  → chạy cả KR2
    └── skills/
        ├── code-map/                 KR1 bước 1 — đọc code
        ├── impact-analysis/          KR1 bước 2 — dựng quan hệ màn hình ↔ file
        ├── code-map-doc/             KR1 bước 3 — vẽ bản đồ
        ├── pr-diff/                  KR2 bước 1 — đọc phần code PR vừa sửa
        ├── blast-radius/             KR2 bước 2 — đối chiếu với bản đồ KR1
        ├── pr-check/                 KR2 bước 3 — phát hiện lỗi
        └── pr-report/                KR2 bước 4 — tạo báo cáo, xếp loại rủi ro
```

> Thư mục `.claude` bị ẩn. Trên Mac, bấm `Cmd + Shift + .` trong Finder để hiện.

Mỗi thư mục skill có cùng một kiểu:

```
<tên-skill>/
├── SKILL.md          hướng dẫn cho Claude: skill làm gì, chạy thế nào, đọc kết quả ra sao, giới hạn
└── scripts/
    ├── <tên>.sh      điểm chạy (kiểm có python3 rồi gọi file .py)
    └── <tên>.py      phần xử lý thật
```

Mọi kết quả ghi vào thư mục `.ai/code-map/` trong repo, chia 3 nơi:

| Thư mục | Chứa |
|---|---|
| `.ai/code-map/raw/` | dữ liệu đọc thẳng từ code (`scan.json`, `pr-diff.json`) |
| `.ai/code-map/analysis/` | dữ liệu đã phân tích (`impact.json`, `blast-radius.json`, `pr-check.json`, `pr-report.json`) |
| `.ai/code-map/doc/` | thứ để người đọc (`index.html`, `summary.md`, `pr-report.md`) |

---

## 2. Cài đặt

1. Chép vào repo:
   ```bash
   cp -R Skill/.claude/skills/*      <repo>/.claude/skills/
   cp    Skill/.claude/commands/*.md <repo>/.claude/commands/
   cp    Skill/.claude/code-map.json <repo>/.claude/      # chỉ khi repo là zaico_web
   ```
2. Cần có `python3` (3.8 trở lên) và `git`. Chỉ dùng thư viện chuẩn: **không cần pip, gem, npm hay Docker**.
3. Thêm `.ai/` vào `.gitignore` — đây là dữ liệu sinh ra, dựng lại được bất cứ lúc nào.

---

## 3. KR1 — Bản đồ code

**Mục đích:** biết mỗi màn hình (route → controller → service → model → view) chạm tới những file nào, và ngược lại, sửa một file thì những màn hình nào bị ảnh hưởng. Đây là nền cho KR2 (review PR) và KR3 (sinh test theo phạm vi).

**Chạy nhanh:** trong Claude Code gõ `/map` (lần đầu tự chạy đủ 3 bước), hoặc:

```bash
.claude/skills/code-map/scripts/scan-code-map.sh             # 1. đọc code        (~6 giây với zaico)
.claude/skills/impact-analysis/scripts/analyze-impact.sh     # 2. dựng quan hệ    (~2 giây)
.claude/skills/code-map-doc/scripts/build.py --open          # 3. vẽ và mở bản đồ (~1 giây)
```

| Lệnh | Làm gì |
|---|---|
| `/map` | dựng bản đồ từ dữ liệu đã có rồi mở |
| `/map --refresh` | đọc lại code trước (chỉ đọc phần đổi kể từ lần trước) |
| `/map --lang ja` | giao diện tiếng Nhật (mặc định tiếng Việt) |
| `/map <từ khoá>` | mở thẳng vào màn hình khớp từ khoá |

### 3.1 `skills/code-map/` — Bước 1: đọc code

| | |
|---|---|
| **Mục đích** | Đọc source, ghi lại **sự thật thô**: có route nào, route trỏ vào hàm nào, mỗi file định nghĩa class/hàm gì, file nào tham chiếu file nào. Không gom nhóm, không kết luận. |
| **Đọc vào** | toàn bộ source của repo |
| **Ghi ra** | `.ai/code-map/raw/scan.json` |
| **File** | `scan-code-map.sh` (điểm chạy, tự chọn bộ đọc) · `scan_code_map.py` (bộ đọc Rails) · `scan_generic.py` (bộ đọc chung cho mọi ngôn ngữ) |

Có **2 bộ đọc**, `scan-code-map.sh` tự chọn. Cả hai ghi ra cùng một định dạng, nên bước 2 và 3 không cần biết bộ nào đã chạy.

| Bộ đọc | Khi nào | Đọc được gì |
|---|---|---|
| **Rails** (`scan_code_map.py`) | repo có `config/routes.rb` và `app/controllers/` | Đầy đủ DSL route (`resources`, `namespace`, `member`…), `before_action`, view và partial, Packwerk pack, trang Vue → API nào |
| **Chung** (`scan_generic.py`) | mọi repo khác | Route theo bảng luật, nối file bằng `import` và tên class, kể cả service tiêm qua constructor |

Bộ đọc chung đã có sẵn luật cho: **Laravel, Symfony, Express, Koa, Fastify, NestJS, Spring, Ktor, ASP.NET, FastAPI, Flask, Django (+ DRF), Go (net/http, Gin, Echo, Chi, Fiber), Sinatra, axum, actix**. Mỗi framework đã thử trên app RealWorld của nó (19 API giống nhau): tìm đủ API, đúng hàm xử lý. Đọc được 12 ngôn ngữ: Ruby, Python, JS/TS, PHP, Java, Kotlin, C#, Go, Rust, Swift, Dart, Scala.

- **Framework nội bộ hoặc cách viết lạ:** không sửa script, chỉ thêm một luật route vào `.claude/code-map.json`. Claude tự làm việc này khi thấy số route bằng 0 hoặc quá ít (cách làm trong `code-map/SKILL.md`).
- **Project không có route** (công cụ dòng lệnh, worker): lấy hàm `main` làm điểm vào.
- Ép chọn bộ đọc: `--scanner rails|generic`, hoặc `"scanner": "generic"` trong `.claude/code-map.json`.

### 3.2 `skills/impact-analysis/` — Bước 2: dựng quan hệ

| | |
|---|---|
| **Mục đích** | Từ dữ liệu thô, trả lời: mỗi màn hình (`controller#action`) chạm tới file nào, qua mấy bước; file nào nhiều màn hình dùng chung; màn hình nào liên quan màn hình nào; file nào không màn hình nào chạm tới. |
| **Đọc vào** | chỉ `scan.json` — không đọc source |
| **Ghi ra** | `.ai/code-map/analysis/impact.json` |
| **File** | `analyze-impact.sh` · `analyze_code_map.py` |

Những khái niệm chính:
- **Màn hình** = một `controller#action`. Nhiều URL cùng trỏ về một action thì gộp làm một.
- **File hub** = file bị 40 file khác trở lên tham chiếu (vd `ApplicationRecord`, `company.rb`). Vẫn ghi nhận là "chạm tới", nhưng không lần tiếp qua nó, vì nếu lần tiếp thì màn hình nào cũng chạm cả ứng dụng.
- **Cây gọi** (`flow`) = ai gọi ai, bắt đầu từ riêng action đó.
- **Màn hình liên quan** = cùng chạm các file không phải hub (tính kiểu cosine, điểm 0–1).

### 3.3 `skills/code-map-doc/` — Bước 3: vẽ bản đồ

| | |
|---|---|
| **Mục đích** | Biến `impact.json` thành một trang HTML xem được ngay (không cần cài gì), và một bản tóm tắt dạng chữ. Màn hình được **đặt tên tiếng Việt** theo từ điển, thay vì `controller#action`. |
| **Đọc vào** | chỉ `impact.json` + `.claude/code-map.json` |
| **Ghi ra** | `.ai/code-map/doc/index.html` (bản đồ) · `.ai/code-map/doc/summary.md` (tóm tắt) |
| **File** | `scripts/build.py` · `assets/app.html` (khung trang, dữ liệu được chèn vào) |

- **Bản đồ** (`index.html`): cây Nhóm tính năng → Controller → Màn hình → Đường dẫn / Code chạy / Dữ liệu & nghiệp vụ / Ảnh hưởng theo. Có ô tìm kiếm, lọc theo module, bảng chi tiết, nút **VI / EN**.
- **Tóm tắt** (`summary.md`): các nhóm tính năng, controller theo từng nhóm, nhóm nào dùng chung file với nhóm nào, file rủi ro nhất khi sửa, route trỏ vào controller không tồn tại, file không ai dùng. Dùng để đọc trong terminal, dán vào review, hoặc so giữa hai lần quét.

### 3.4 `code-map.json` — cấu hình riêng của từng repo

Mọi thứ chỉ đúng cho zaico đều nằm ở file này, nên skill dùng lại được cho repo khác mà không phải sửa script. **Mọi khoá đều không bắt buộc**: không có file này thì bản đồ vẫn dựng được, chỉ có điều màn hình giữ tên gốc `controller#action` và mọi thứ gom vào một nhóm.

| Khoá | Dùng để |
|---|---|
| `labels` | từ điển tên tiếng Việt (`overrides`, `namespaces`, `actions`, `verbs`, `nouns`, `terms`) |
| `features` / `namespace_features` / `other_area` | xếp controller vào nhóm tính năng (Tồn kho, Mua hàng, Bán hàng…) |
| `areas_en` | tên tiếng Anh của từng nhóm (cho nút EN) |
| `hidden_controllers` | controller không hiện ở trang tổng quan (API, trang admin) |
| `default_screen` | màn hình mở sẵn khi vào bản đồ |
| `scanner` | ép chọn bộ đọc `rails` / `generic` |
| `routes.rules` / `routes.disable` | thêm luật route cho framework lạ, hoặc tắt luật có sẵn |
| `scan.include` / `scan.exclude` | giới hạn file mà bộ đọc chung đọc |

---

## 4. KR2 — Review PR theo phạm vi ảnh hưởng

**Mục đích:** phát hiện vấn đề tiềm ẩn trước khi senior review, và review phân tầng theo rủi ro: PR đụng file dùng chung thì phải đọc kỹ, PR chỉ sửa spec thì review thường là đủ.

**Cần có trước:** bản đồ KR1 (`impact.json`). Chưa có thì chạy `/map` trước.

**Chạy nhanh:** trong Claude Code gõ `/review-pr`. Claude chạy cả 4 bước, **mở code xác nhận từng lỗi**, rồi báo lại. Hoặc chạy từng bước:

```bash
.claude/skills/pr-diff/scripts/scan-pr-diff.sh                # 1. PR sửa dòng nào
.claude/skills/blast-radius/scripts/analyze-blast-radius.sh   # 2. đụng bao nhiêu màn hình
.claude/skills/pr-check/scripts/check-pr.sh                   # 3. rà lỗi
.claude/skills/pr-report/scripts/build-pr-report.sh           # 4. gộp báo cáo
```

| Lệnh | Làm gì |
|---|---|
| `/review-pr` | review nhánh hiện tại so với điểm tách khỏi master |
| `/review-pr <base>` | so với một mốc cụ thể (commit / nhánh / tag) |
| `/review-pr --lang en` | viết báo cáo bằng tiếng Anh |

### 4.1 `skills/pr-diff/` — Bước 1: đọc code PR vừa sửa

| | |
|---|---|
| **Mục đích** | Ghi lại chính xác PR sửa gì: file nào, từ dòng nào tới dòng nào, mỗi dòng sửa nằm trong class / method nào, method nào là mới hoàn toàn. Không phán đúng sai. |
| **Đọc vào** | git diff |
| **Ghi ra** | `.ai/code-map/raw/pr-diff.json` |
| **File** | `scan-pr-diff.sh` · `scan_pr_diff.py` |
| **Tuỳ chọn** | `--base <ref>` (mốc so sánh) · `--working` (code chưa commit) · `--staged` (đã `git add`) · `--pr 1234` |

Ghi tên method theo đúng cách KR1 gọi (vd `Api::InventoryVariantsController#available_variants`), nhờ vậy bước 2 mới tra ngược vào bản đồ được.

### 4.2 `skills/blast-radius/` — Bước 2: đối chiếu với bản đồ KR1

| | |
|---|---|
| **Mục đích** | Lấy danh sách file PR sửa, tra vào bản đồ KR1 để biết mỗi file có bao nhiêu màn hình chạm tới, file nào là file dùng chung, màn hình nào bị sửa thẳng vào action của nó, file nào bản đồ không biết. |
| **Đọc vào** | `pr-diff.json` + `impact.json` — không đọc source, không chạy git |
| **Ghi ra** | `.ai/code-map/analysis/blast-radius.json` |
| **File** | `analyze-blast-radius.sh` · `analyze_blast_radius.py` |

Mức rủi ro của từng file, tính theo tỉ lệ màn hình chạm tới:

| Mức | Khi nào |
|---|---|
| `critical` | chạm ≥ 30% số màn hình |
| `high` | ≥ 5%, hoặc là file hub |
| `medium` | ≥ 1% |
| `low` | có ít nhất 1 màn hình |
| `unknown` | bản đồ không biết file này (spec, doc…) |

Nếu bản đồ được quét ở commit khác với PR thì kết quả ghi `map_possibly_stale: true`, và số màn hình khi đó chỉ là ước lượng.

### 4.3 `skills/pr-check/` — Bước 3: phát hiện lỗi

| | |
|---|---|
| **Mục đích** | Rà 3 nhóm vấn đề trong phần code PR vừa thêm. Script chỉ đưa **nghi vấn có bằng chứng**; sau đó Claude mở code để xác nhận. |
| **Đọc vào** | `pr-diff.json` (+ `blast-radius.json` để xếp lỗi ở file dùng chung lên trước) + source |
| **Ghi ra** | `.ai/code-map/analysis/pr-check.json` |
| **File** | `check-pr.sh` · `check_pr.py` (điều phối) · `checks_breaks.py` · `checks_duplicate.py` · `checks_rules.py` · `common.py` |

| Nhóm | File | Rà gì |
|---|---|---|
| **Phá chỗ khác** | `checks_breaks.py` | xoá / đổi tên method, class, action, key dịch mà nơi khác còn dùng · đổi tham số mà chỗ gọi chưa sửa · sửa method trong file nhiều màn hình dùng chung · method public mới chưa có test |
| **Code lặp / thừa** | `checks_duplicate.py` | method giống hệt method có sẵn · từ 6 dòng chép y nguyên · method mới không ai gọi · code bị comment lại |
| **Sai quy chuẩn** | `checks_rules.py` | cấm mock/stub trong spec · controller không query · không để logic nghiệp vụ trong Model · không tính toán trong SQL · không đặt điều kiện cố định trong vòng lặp · Service không gọi Service · Ruby 3.3 · `binding.pry` · `any` · `v-html` · chữ Nhật viết cứng · GTM · Packwerk · format commit |

- Mỗi phát hiện có: mức độ (`error` = phá / bị cấm, `warning` = nhiều khả năng sai, `info` = để người quyết), `file:dòng`, một câu giải thích (VI + EN), bằng chứng (chỗ gọi, đoạn trùng, dòng code).
- Chỉ soi **phần PR thêm vào**, trừ nhóm "phá chỗ khác" phải quét cả repo để tìm chỗ còn gọi.
- Muốn bỏ qua một dòng cố ý viết như vậy: thêm comment `pr-check:ignore <lý do>`.

### 4.4 `skills/pr-report/` — Bước 4: tạo báo cáo

| | |
|---|---|
| **Mục đích** | Gộp 3 bước trên thành một báo cáo đọc từ trên xuống: kết luận rủi ro kèm lý do, phạm vi thay đổi, file nào nên đọc trước, danh sách phát hiện kèm kết luận của Claude. |
| **Đọc vào** | `pr-diff.json` + `blast-radius.json` + `pr-check.json` — không đọc source, không chạy git |
| **Ghi ra** | `.ai/code-map/doc/pr-report.md` (để đọc, dán vào PR) · `.ai/code-map/analysis/pr-report.json` (cùng nội dung, cho máy đọc) |
| **File** | `build-pr-report.sh` · `build_pr_report.py` |
| **Tuỳ chọn** | `--lang vi\|en` · `--verdicts <file.json>` (gắn kết luận sau khi đọc code) |

Xếp loại PR:

| Mức | Khi nào |
|---|---|
| **Cao** | có `error`, hoặc đụng file dùng chung mức critical/high mà còn có `warning` |
| **Vừa** | đụng file dùng chung mức critical/high, hoặc có `warning` |
| **Thấp** | không có cả hai |
| **Chưa kết luận** | chưa chạy bước 3 — chưa rà thì không được nói là an toàn |

- Bảng **"Đọc theo thứ tự này"**: file nhiều lỗi và nhiều màn hình dùng chung được đưa lên đầu.
- Cột **"Xác nhận"**: Claude ghi *lỗi thật / báo nhầm / cần hỏi người viết* kèm một câu lý do. Phát hiện nào chưa ai kiểm thì ghi **"chưa đọc"**.

---

## 5. Dùng cho repo khác

| Phần | Repo Rails khác | Repo không phải Rails |
|---|---|---|
| KR1 (cả 3 bước) | Dùng được, viết `code-map.json` riêng để có tên tiếng Việt và nhóm tính năng | Dùng được qua bộ đọc chung |
| KR2 bước 1, 2, 4 | Dùng được | Dùng được, nhưng bước 1 chỉ tách được class/method cho Ruby; ngôn ngữ khác chỉ ghi ở mức file |
| KR2 bước 3 | "Phá chỗ khác" và "Code lặp" dùng được; nhóm **"Sai quy chuẩn" là luật riêng của zaico** (`.claude/rules`, `CLAUDE.local.md`), phải viết lại cho team khác | Như bên trái |

---

## 6. Giới hạn chung

- **Phân tích tĩnh, theo tên**: không chạy code. `send`, `constantize`, reflection, key dịch ghép động, DI cấu hình bằng file sẽ không thấy. **Không có phát hiện không có nghĩa là an toàn.**
- **Không chạy** rubocop, eslint, test hay type-check — đó là việc của CI.
- **Lỗi logic** (vd `>` thành `>=`) nằm ngoài tầm của mọi bước máy, phải người hoặc Claude đọc code.
- **Số màn hình chỉ chính xác** khi bản đồ KR1 được quét ở cùng commit với PR. Nếu không, chạy `/map --refresh` trước `/review-pr`.
- Một phần luật ở KR2 bước 3 lấy từ `CLAUDE.local.md` — file local của team, không có trong repo.
