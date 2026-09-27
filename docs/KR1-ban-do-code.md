# KR1 — Bản đồ code (chi tiết)

> Tổng quan ở `../README.md`. **Mỗi Target của KR1 được skill nào làm và đã đạt tới đâu: xem `../README.md` mục 0 "Đối chiếu với mục tiêu KR".**
> File này giải thích từng bước **chạy bên trong ra sao**,
> **từng trường** trong các file JSON, và **cách đọc** bản đồ.
> Mọi ví dụ lấy từ lần chạy thật trên zaico_web, commit `20626c5b` (nhánh KAIZ-384).

---

## 0. Bức tranh chung

```
source code ──► [1] code-map ──► scan.json ──► [2] impact-analysis ──► impact.json ──► [3] code-map-doc ──► index.html
                    đọc code        sự thật thô      dựng quan hệ          màn hình ↔ file      vẽ bản đồ          summary.md
```

Mỗi bước **chỉ đọc file của bước trước**, không đọc lại source. Nhờ vậy:

- Muốn sửa cách đặt tên hay màu sắc thì chỉ chạy lại bước 3 (khoảng 1 giây).
- KR2 (review PR) và KR3 (sinh test) dùng chung `impact.json`, không phải quét lại code.
- Mỗi bước có ranh giới rõ, sai ở đâu thì tìm ở đó.

| Bước | Được làm | **Không** được làm |
|---|---|---|
| 1 code-map | đọc source, ghi lại sự thật | gom nhóm, kết luận, viết tài liệu cho người đọc |
| 2 impact-analysis | nối các sự thật lại, tính toán | đọc source |
| 3 code-map-doc | đặt tên, trình bày | tính toán, đọc source |

Số liệu với zaico: **1.572 route → 1.498 màn hình**, 409 controller, 2.348 file được chạm tới, trung bình **30,7 file / màn hình**, 22 file hub.

---

## 1. Bước 1 — `code-map`: đọc code

### 1.1 Chọn bộ đọc

`scan-code-map.sh` quyết định theo thứ tự:

1. Có `--scanner rails|generic` trên dòng lệnh → dùng cái đó.
2. Có `"scanner"` trong `.claude/code-map.json` → dùng cái đó.
3. Có `config/routes.rb` **và** `app/controllers/` → **rails**.
4. Còn lại → **generic**.

### 1.2 Bộ đọc Rails (`scan_code_map.py`)

Đọc theo đúng quy ước của Rails, **không khởi động Rails** (không cần database, không cần Docker):

| Đọc gì | Từ đâu | Lấy ra |
|---|---|---|
| Route | `config/routes.rb`, `config/routes/*.rb`, `packs/*/config/routes/` | method, path, controller, action. Hiểu `resources`, `resource`, `namespace`, `scope`, `member`, `collection`, `draw`, `devise_for`, `only:` / `except:`, `module:`, `controller:` |
| Controller | `app/controllers/**`, `packs/*/app/controllers/**` | class, class cha, các action public, `before_action`, `render` |
| Model | `app/models/**` | `belongs_to` / `has_many`…, `scope`, `enum`, `table_name` |
| Service / Job / Mailer / Lib | các thư mục tương ứng | class, method public |
| View | `app/views/**` | view thuộc controller/action nào, partial nào được `render` |
| Trang Vue | `app/frontend/javascripts/pages/**` | khoá trang (`<controller>/<action>`) để nối với màn hình |
| Frontend → API | `api/endpoints/*.ts` và mọi file `.vue` / `.ts` | trang nào gọi hàm API nào, hàm đó gọi URL nào |
| Pack | `packs/*/package.yml` | tên pack, pack phụ thuộc pack nào |

**Tham chiếu giữa các file** được tìm như sau:

- Bỏ comment và nội dung chuỗi (tránh bắt nhầm chữ `"Service unavailable"` thành class `Service`).
- Tìm mọi tên hằng (`InventoryVariant`, `Api::BaseController`…) có tồn tại trong danh sách class.
- Tên ngắn như `Client` chỉ được tính nếu **đúng một** class mang tên đó; nếu nhiều class trùng tên thì bỏ, không đoán.
- Lối gọi kiểu association (`current_company.categories.find`) được nối về model `Category`, trừ các tên quá chung như `name`, `count`, `status`.

**Tham chiếu theo từng action**: với controller, bộ đọc cắt riêng thân của mỗi action, cộng thêm các `before_action` áp vào action đó, và lần tiếp tối đa 3 tầng vào các hàm private mà chúng gọi. Nhờ vậy `inventories#index` không bị gán luôn phần việc của `inventories#destroy`.

**Quét tăng dần** (`--since <commit>`): chỉ đọc lại những file đổi kể từ commit đó (lấy từ `git diff`), rồi tính lại tham chiếu cho toàn bộ. Route chỉ đọc lại khi file route bị đổi.

### 1.3 Bộ đọc chung (`scan_generic.py`)

Dùng cho mọi repo không phải Rails. Gồm 5 phần:

**a. Chọn file.** Mọi file có đuôi thuộc 12 ngôn ngữ trong bảng `LANGS`. Bỏ qua:
- thư mục thư viện / build / tạm: `node_modules`, `vendor`, `dist`, `build`, `target`, `bin`, `obj`, `.venv`, `__pycache__`, `migrations`…
- thư mục và file test: `test/`, `tests/`, `spec/`, `__tests__/`, `*_test.go`, `*.spec.ts`, `test_*.py`, `*Test.java`…

Thu hẹp thêm bằng `scan.include` / `scan.exclude` trong config.

**b. Đọc từng file.** Một bộ tách token chung cho mọi ngôn ngữ, làm ra 2 bản sao của file, **giữ nguyên vị trí từng ký tự**:
- `code`: bỏ comment, giữ chuỗi. Dùng để đọc route, import, khai báo.
- `bare`: bỏ cả comment lẫn nội dung chuỗi. Dùng để tìm tên và ghép ngoặc.

Rồi đọc: namespace / package, các class (và class cha), các hàm, các import.

**c. Nối các file.** Có 3 nguồn:

| Nguồn | Ví dụ |
|---|---|
| import trỏ được tới file | `import { x } from './a'`, alias `@/` trong `tsconfig`, `from app.api.routes import users` (Python, kể cả module con), `import io.spring.core.Article;` (Java theo tên đầy đủ), `use App\Models\User;` (PHP), module trong `go.mod` (Go), `mod x;` (Rust) |
| tên class dùng trong code | chỉ tính khi đúng 1 file khai báo tên đó; nếu nhiều file trùng tên thì chọn file **gần nhất về đường dẫn**, miễn là gần hơn hẳn |
| biến tiêm qua constructor | `private final ArticleService articleService;` → dùng `articleService` tức là dùng `ArticleService` |

**d. Tìm route.** Theo bảng luật `ROUTE_RULES`. Có 2 kiểu luật:

| Kiểu | Nghĩa | Ví dụ |
|---|---|---|
| `call` | một câu lệnh khai báo route và chỉ ra hàm xử lý | `Route::get('users', 'UserController@index')` · `router.get('/users', listUsers)` · `path('users/', views.user_list)` · `r.GET("/users", ListUsers)` |
| `decorator` | nhãn đặt ngay trên hàm xử lý; nhãn trên class là tiền tố URL | `@GetMapping("/users")` · `@Get()` · `[HttpGet]` · `@router.get("/users")` |

Những trường hợp đặc biệt đã xử lý:
- **Laravel `Route::resource`** được bung ra 7 route (`index`, `create`, `store`, `show`, `edit`, `update`, `destroy`), có tính `only` / `except`.
- **Django class-based view** (`View.as_view()`) được bung ra theo các hàm `get` / `post` / `list` / `retrieve`… mà class tự viết hoặc kế thừa từ `ListCreateAPIView`, `RetrieveUpdateAPIView`…
- **DRF `router.register`** được bung theo viewset và các mixin.
- **Tiền tố gắn ở file khác**: FastAPI `include_router(prefix=)`, Flask `register_blueprint(url_prefix=)`, Express `app.use('/api', r)`, Gin `r.Group("/api")` được truyền vào hàm đăng ký. Đi qua nhiều tầng file vẫn ghép đúng.
- **Hàm nạp chồng** (`Get()` và `Get(slug)` trong C#/Java) được tách thành 2 màn hình `Get` và `Get_2`.
- **Hàm viết thẳng trong lời gọi** (`router.get('/x', async (req) => {...})`) vẫn thành màn hình, tên đặt theo method + đường dẫn (`get_articles_feed`).

**e. Không có route nào:** mỗi điểm vào (`if __name__ == "__main__"`, `func main`, `static void main`, `main` / `bin` trong `package.json`…) trở thành một màn hình, lấy cả file làm thân.

**Tên màn hình** ở bộ đọc chung: `<controller>#<hàm>`, trong đó `<controller>` là tên class bỏ đuôi `Controller` / `Api` / `View`… rồi viết thường có gạch dưới (`ArticlesApi` → `articles`), hoặc tên file nếu hàm không nằm trong class (`auth.controller.ts` → `auth`).

### 1.4 Các trường trong `scan.json`

```
scan.json
├── meta
├── packs[]
├── routes[]              route → controller#action
├── routes_files[]        file route đã đọc
├── controllers[]  ┐
├── models[]       │
├── services[]     │      mỗi file code một phần tử
├── jobs[]         │
├── mailers[]      │
├── lib[]          ┘
├── views[]               (chỉ Rails)
├── frontend_pages[]      (chỉ Rails)
├── frontend_api[]        (chỉ Rails)
└── frontend_modules[]    (chỉ Rails)
```

**`meta`**

```json
{"schema_version": 1, "generated_at": "2026-09-27T09:31:53Z",
 "commit": "20626c5b88e9…", "previous_commit": "20626c5b88e9…", "mode": "incremental",
 "root": ".", "routes_source": "static-parse", "files_reparsed": 0}
```

| Trường | Nghĩa |
|---|---|
| `schema_version` | phiên bản định dạng. Bước 2 từ chối chạy nếu không khớp |
| `commit` | commit lúc quét. **KR2 dùng để biết bản đồ có cũ so với PR không** |
| `previous_commit` / `files_reparsed` | với quét tăng dần: tính từ commit nào, đọc lại bao nhiêu file |
| `mode` | `full` hoặc `incremental` |
| `routes_source` | đọc route bằng gì: `static-parse` (Rails), `rails-routes` (từ file `rails routes`), `generic:laravel,…` (luật nào đã khớp), `generic:entry-points` |
| `scanner`, `languages` | (chỉ bộ đọc chung) và số file theo từng ngôn ngữ |

**`routes[]`** — ví dụ:

```json
{"method": "GET",
 "path": "/(/companies/:company_id)/api/inventory_variants/available_variants",
 "controller": "api/inventory_variants", "action": "available_variants",
 "controller_class": "Api::InventoryVariantsController",
 "source_file": "config/routes.rb", "source_line": 1299,
 "source": "static-parse", "raw": "get :available_variants, on: :collection"}
```

`controller_class` là khoá để bước 2 tìm ra file controller. `source_file:source_line` và `raw` cho biết route được khai báo ở dòng nào, để kiểm lại khi thấy nghi.

**`controllers[]`** — mỗi file controller một phần tử:

| Trường | Ví dụ (`api/inventory_variants_controller.rb`) |
|---|---|
| `file`, `pack`, `class`, `superclass` | `Api::InventoryVariantsController` < `Api::BaseController` |
| `actions` | `index`, `total`, `bulk_update`, `destroy`, `available_variants`, `suggest` |
| `before_actions` | `restrict_usable_action`, `restrict_editorial_action`, … |
| `references` | mọi file mà cả controller tham chiếu tới |
| `action_references` | **theo từng action**. `available_variants` → `search_available_inventory_variant_service.rb`, `inventory.rb`, `inventory_master.rb` |
| `filter_references` | mỗi `before_action` áp cho action nào (`only` / `except`) và tham chiếu tới đâu |
| `loc` | số dòng |

**`models[]`** có thêm `table_name`, `associations` (`belongs_to :company`…), `scopes`, `enums`. **`services[]`, `jobs[]`, `mailers[]`, `lib[]`** có `class`, `public_methods`, `references`.

**`views[]`**: `controller_hint` / `action_hint` (view thuộc màn hình nào), `partial`, `renders` (các partial nó vẽ).

**`frontend_*`**: `frontend_pages` (trang Vue và khoá trang), `frontend_api` (hàm API → method + URL), `frontend_modules` (mỗi file .vue/.ts import gì, gọi hàm API nào, tiêu đề tiếng Nhật trên trang).

---

## 2. Bước 2 — `impact-analysis`: dựng quan hệ

### 2.1 Làm gì, theo thứ tự

1. **Màn hình** = gom route theo `controller#action`. 1.572 route → 1.498 màn hình.
2. **Tìm file controller** của màn hình qua `controller_class`. Không tìm được thì ghi vào `unresolved_controllers` (route trỏ vào controller không tồn tại). Route của Rails / gem (`rails/`, `devise/`, `active_storage/`…) được đếm riêng, không coi là lỗi.
3. **Tìm file hub**: file bị từ **40** file khác trở lên tham chiếu (`--hub-threshold`).
4. **Lần theo tham chiếu** từ file controller, theo chiều rộng, tối đa **3 tầng** (`--max-depth`). Gặp hub thì ghi nhận là "chạm tới" nhưng **không lần tiếp qua nó**. Nếu lần qua `ApplicationRecord` thì màn hình nào cũng chạm cả ứng dụng, và bản đồ mất hết ý nghĩa.
5. **View** của màn hình: view cùng controller/action, cộng mọi partial được nó `render` (lần tiếp tối đa 4 tầng).
6. **Trang Vue** của màn hình: theo khoá `<controller>/<action>`.
7. **Cây gọi** (`flow`): bắt đầu từ `action_references` của riêng action đó (không phải cả controller), lần tiếp vào service / job / lib, dừng ở model (điểm cuối: "đụng dữ liệu nào"), tối đa 4 tầng, 40 nhánh mỗi nút. Bỏ qua lớp cha (`ApplicationRecord`, `BaseService`) và file `errors.rb`, vì chúng là hạ tầng chứ không phải một bước trong luồng.
8. **Trang Vue → API**: từ trang, đi theo import (bỏ qua layout, store, utils dùng chung) tới hàm API, rồi so URL với route để biết nút này gọi vào màn hình nào. Ghi ngược lại vào màn hình đích là `called_from`.
9. **Chỉ mục ngược**: file → những màn hình chạm tới nó.
10. **Màn hình liên quan**: hai màn hình liên quan khi cùng chạm những file **không phải hub** và không quá phổ biến (≤ 150 màn hình). File càng ít màn hình dùng thì càng có trọng lượng. Điểm từ 0 tới 1 (kiểu cosine); giữ tối đa 12 màn hình, điểm ≥ 0,05.
11. **File không ai chạm**: file code không màn hình nào chạm tới.

### 2.2 Các trường trong `impact.json`

**`stats`**

```json
{"screens": 1498, "routes": 1572, "hub_files": 22, "screens_with_vue_page": 182,
 "files_reached": 2348, "files_unreached": 433, "unresolved_controllers": 14,
 "framework_routes": 2, "avg_files_per_screen": 30.7, "screens_with_related": 1465}
```

**`screens[]`** — mỗi màn hình một phần tử:

| Trường | Nghĩa / ví dụ (`api/inventory_variants#available_variants`) |
|---|---|
| `id`, `controller`, `action`, `controller_file`, `pack` | định danh màn hình |
| `action_defined` | action có thật trong file controller không (`false` = action kế thừa, hoặc route trỏ vào action không có) |
| `routes[]` | các URL trỏ về màn hình này |
| `views[]` | các view / partial |
| `vue_page` | trang Vue của màn hình, nếu có |
| `code_files[]` | **mọi file chạm tới**, kèm `depth` (cách mấy bước; 0 là chính controller) và `hub`. Ví dụ này có 59 file |
| `code_file_count` | số file ở trên |
| `view_tree[]` | view và các partial nó vẽ, dạng cây |
| `flow[]` | cây gọi của riêng action: `search_available_inventory_variant_service.rb` → `inventory_assignable_quantity_calculable.rb` → `purchase_item_variant.rb` … |
| `related_screens[]` | `{id, score}`; ví dụ `api/inventory_variants#export` 0,8154 |
| `api_calls[]` | (màn hình có trang Vue) trang gọi API nào: `method`, `path`, `fn`, `screen` đích, `via` (đi qua file nào) |
| `called_from[]` | (màn hình là API) được trang nào gọi |
| `title_ja` | tiêu đề tiếng Nhật trên màn hình thật |

**`code_files` khác `flow` ở chỗ nào**:
- `code_files` là **phạm vi cả controller** (phẳng). **KR2 dùng cái này** để tính "sửa file X thì đụng màn hình nào", vì muốn an toàn thì thà tính rộng.
- `flow` là **luồng của riêng action** (dạng cây). Dùng để hiểu và đọc; bản đồ vẽ theo cái này.

**`shared_files[]`**: file → số màn hình chạm tới (`screen_count`), tỉ lệ (`screen_ratio`), có phải hub không, số file tham chiếu tới (`raw_fan_in`), 20 màn hình đầu. Xếp theo số màn hình giảm dần. Đứng đầu là `application_record.rb` (1.226 màn hình).

**`hubs[]`**: file hub và số file tham chiếu tới. Ba file đầu: `base_service.rb` (435), `application_record.rb` (256), `company.rb` (172).

**`unreached_files[]`**, **`unresolved_controllers[]`**, **`packs`** (số màn hình và số file theo pack).

---

## 3. Bước 3 — `code-map-doc`: vẽ bản đồ

### 3.1 Đặt tên tiếng Việt

`controller#action` thì chính xác nhưng khó đọc, nên bước 3 dịch sang tiếng Việt theo từ điển trong `.claude/code-map.json`. Từ điển zaico hiện có: 20 tên cố định, 71 action, 60 động từ, 267 danh từ, 265 thuật ngữ, 32 namespace.

Quy tắc, theo thứ tự ưu tiên:

1. **Tên cố định** (`overrides`): khớp nguyên `id` thì lấy luôn. `users/sessions#new` → **Đăng nhập**.
2. Còn lại, tên = **{động từ của action} + {danh từ của controller}**:
   - Action có trong `actions` → dùng luôn. `index` → *Danh sách*, `show` → *Chi tiết*.
   - Không có → ghép từ động từ đầu (`verbs`) và các từ phía sau (`terms`). `download_receipt` → *Tải xuống biên lai*.
   - Danh từ lấy từ đoạn cuối của đường dẫn controller (`nouns`). Bỏ qua đoạn phiên bản (`v1`, `_v2`).
   - Các đoạn phía trước thành phần phụ (`namespaces`): `api` → *API*, `stocktakings` → *Kiểm kê*.
   - Nếu động từ và danh từ trùng chữ ở chỗ nối thì chỉ giữ một lần.
3. **Có từ nào không nằm trong từ điển thì giữ nguyên tên gốc** — đoán bừa còn tệ hơn để tên thật.

Ví dụ thật:

| `id` | Tên hiển thị | Nguồn |
|---|---|---|
| `inventories#index` | Danh sách tồn kho | từ điển |
| `purchases#split` | Tách mua hàng | từ điển |
| `api/inventory_variants#available_variants` | Available variants biến thể tồn kho · API | ghép một phần (`available`, `variants` chưa có trong `terms`) |

Tỉ lệ màn hình có tên hoàn toàn từ từ điển được in ra mỗi lần dựng. Zaico hiện là **1.194 / 1.498 = 80%**; phần còn lại là tên ghép, có chỗ đọc hơi lạ. Muốn tăng tỉ lệ này: thêm từ vào các bảng `labels` trong `.claude/code-map.json` rồi chạy lại `/map` (khoảng 1 giây, không phải quét lại code).

File trong cây gọi cũng được đặt tên tiếng Việt theo cùng từ điển. Ví dụ `search_available_inventory_variant_service.rb` → *Tìm biến thể tồn kho khả dụng…*.

### 3.2 Nhóm tính năng

Mỗi controller được xếp vào một nhóm theo thứ tự:

1. `namespace_features`: đoạn đầu của đường dẫn là một namespace đã khai báo → lấy nhóm đó. Ví dụ mọi controller dưới `purchase_orders/` đều về *Mua hàng*, kể cả `purchase_orders/catalogs`, dù đuôi nghe như bán hàng.
2. `features`: regex khớp **đoạn cuối** của đường dẫn, rồi tới **đoạn đầu**. Luật nào khớp trước thì thắng.
3. Không khớp gì → `other_area` (*Khác*). Nhóm này phình to là dấu hiệu cần thêm luật.

Zaico có 13 nhóm; lớn nhất là *Tồn kho* (86 controller, 348 màn hình).

### 3.3 Đọc bản đồ (`index.html`)

- **Trang đầu**: các nhóm tính năng. Bấm một nhóm để mở các controller bên trong. Controller trong `hidden_controllers` (API, trang admin) không hiện ở đây nhưng vẫn tìm được.
- **Chọn một màn hình**: cây mở sang phải, mỗi nhánh trả lời một câu hỏi:

| Nhánh | Câu hỏi |
|---|---|
| **Thuộc nhóm** | màn hình này nằm ở khu nào |
| **Đường dẫn** | vào bằng URL nào |
| **Code chạy** | controller, service nào chạy |
| **Dữ liệu & nghiệp vụ** | đụng tới model nào |
| **Ảnh hưởng theo** | những màn hình khác dùng chung code (sửa ở đây có thể ảnh hưởng tới đó) |

- **Ô tìm kiếm**: tìm theo tên tiếng Việt, `controller#action`, URL, tên file, hoặc tiêu đề tiếng Nhật.
- **Lọc theo module / loại node**, **bảng chi tiết** bên phải, nút **VI / EN**: EN đổi mọi tên về tên gốc trong code, để kiểm lại khi nghi tên tiếng Việt dịch sai.

### 3.4 `summary.md`

Bản chữ của bản đồ, để đọc trong terminal, dán vào review, hoặc so giữa hai lần quét (`diff`):

1. Tổng quan (số route, màn hình, controller, nhóm, file, hub)
2. Nhóm tính năng: số controller, số màn hình
3. Controller theo từng nhóm, và mọi màn hình của nó (URL, số file, trang Vue, action có tồn tại không)
4. Nhóm nào dùng chung file với nhóm nào (cặp đầu bảng: sửa nhóm này dễ làm vỡ nhóm kia nhất)
5. 25 file rủi ro nhất khi sửa (không tính hub)
6. 20 màn hình chạm nhiều file nhất
7. Chỗ cần xem lại: route trỏ vào controller không tồn tại, hub, file không ai chạm (chia theo thư mục)

---

## 4. Các con số có thể chỉnh

| Con số | Mặc định | Chỉnh ở đâu |
|---|---|---|
| Ngưỡng hub | 40 file tham chiếu | `analyze-impact.sh --hub-threshold N` |
| Độ sâu lần theo | 3 tầng | `analyze-impact.sh --max-depth N` |
| Màn hình liên quan: bỏ file phổ biến hơn | 150 màn hình | `RELATED_FILE_CAP` trong `analyze_code_map.py` |
| Màn hình liên quan: số lượng / điểm tối thiểu | 12 / 0,05 | `RELATED_PER_SCREEN` / `RELATED_MIN_SCORE` |
| Cây gọi: độ sâu / số nhánh | 4 / 40 | `FLOW_MAX_DEPTH` / `FLOW_MAX_CHILDREN` |
| Số file vẽ mỗi màn hình, màu, cỡ node | 22 file, … | `UI` trong `build.py` |

---

## 5. Giới hạn

- **Phân tích tĩnh, theo tên.** Không chạy code. Gọi động (`send`, `constantize`, reflection, tên class ghép từ chuỗi) sẽ không thấy. Tên class quá chung có thể bị tính thừa.
- **"Liên quan" = dùng chung file**, không phải "cùng một tính năng" theo cách người hiểu. Đáng tin trong cùng một controller, yếu hơn giữa hai khu xa nhau.
- **File không ai chạm là ứng viên, không phải bằng chứng code chết.** Job, rake task, console, metaprogramming đều không nằm trên đường đi từ route.
- **Thay đổi chưa commit không được thấy** khi quét tăng dần, vì nó so theo commit.
- Bộ đọc chung: tiền tố URL nằm trong hằng số (`prefix=API_PREFIX`) sẽ mất; không có phần view và frontend → API; luôn quét toàn bộ (khoảng 4 giây / 1.000 file).
