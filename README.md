# Messi Golden Boot Alert — Cloud (GitHub Actions + Resend)

Theo dõi tỉ lệ **Messi** thắng World Cup Golden Boot trên Polymarket và **gửi email**
khi tỉ lệ ≥ **63%**. Chạy hoàn toàn trên cloud của GitHub — **không cần treo máy local**.

## Cách hoạt động
- GitHub Actions chạy cron **mỗi ~10 phút** → `messi_cloud_alert.py` gọi API Polymarket.
- Gửi email qua **Resend** theo 3 loại cảnh báo (xem bên dưới).
- `state.json` được commit lại repo để nhớ trạng thái giữa các lần chạy (không cần treo máy).

## Logic cảnh báo
| Tình huống | Email? | Ví dụ |
|-----------|:------:|-------|
| Vừa **vượt 63%** lần đầu | ✅ `cross` | 62% → 63% |
| Đang trên ngưỡng, **lập mốc % nguyên mới cao hơn** | ✅ `rise` | 63% → 64% → 65%… (mỗi mốc 1 email) |
| Vẫn trên ngưỡng nhưng **chưa lập mốc mới** | ❌ | 64.1% → 64.7% (vẫn mốc 64) |
| Vừa **tụt xuống dưới 63%** | ✅ `drop` | 63% → 62% |
| Ở dưới ngưỡng | ❌ | 61% → 60% |

> Sau khi tụt xuống dưới 63% rồi vượt lên lại → tính là chu kỳ mới, `cross` bắn lại từ đầu.
> Nếu giá nhảy vọt bỏ qua mốc (63% → 66% trong 1 lần kiểm tra) → gửi **1 email tại mốc cao nhất** (66%).

## File trong repo
| File | Vai trò |
|------|---------|
| `messi_goldenboot_monitor.py` | Logic lấy tỉ lệ realtime từ Polymarket (chạy local cũng được) |
| `messi_cloud_alert.py` | Kiểm tra 1 lần + gửi email Resend + quản lý state |
| `.github/workflows/messi-monitor.yml` | Lịch cron chạy trên GitHub |
| `state.json` | Tự sinh ra, lưu trạng thái đã cảnh báo (đừng sửa tay) |

## Các bước cài đặt (5 phút)

### 1. Đẩy code lên một repo GitHub
```bash
cd /Users/minhnguyen/Documents/Claude/Projects
git init && git add messi_goldenboot_monitor.py messi_cloud_alert.py .github README-messi-alert.md
git commit -m "Messi golden boot cloud alert"
git branch -M main
git remote add origin git@github.com:<user>/<repo>.git
git push -u origin main
```
> Repo **private** vẫn chạy Actions miễn phí thoải mái cho nhu cầu này.

### 2. Lấy Resend API key
1. Đăng ký tại https://resend.com — **nên đăng ký bằng chính email muốn nhận cảnh báo**
   (ví dụ `minh.nguyen@datanest.vn`).
2. Vào **API Keys** → tạo key (dạng `re_...`).
3. Chế độ mặc định dùng người gửi `onboarding@resend.dev` và **chỉ gửi được tới email bạn
   đã đăng ký Resend**. Muốn gửi tới địa chỉ khác → **Verify a domain** trong Resend rồi đổi
   `ALERT_EMAIL_FROM` sang địa chỉ thuộc domain đó.

### 3. Thêm GitHub Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Giá trị | Bắt buộc |
|--------|---------|:-------:|
| `RESEND_API_KEY` | key `re_...` từ Resend | ✅ |
| `ALERT_EMAIL_TO` | `minh.nguyen@datanest.vn` (nhiều email cách nhau bởi dấu phẩy) | ✅ |
| `ALERT_EMAIL_FROM` | chỉ đặt nếu đã verify domain, vd `Alert <alert@yourdomain.com>` | ❌ |

### 4. Bật & test
- Tab **Actions** → nếu được hỏi thì bấm **I understand my workflows, enable them**.
- Chọn workflow **Messi Golden Boot Monitor** → **Run workflow** để chạy tay 1 lần.
- **Test email:** ở ô **test_threshold** khi bấm Run workflow, nhập `0.50` → gửi 1 email THỬ
  (chạy chế độ TEST_MODE, không đụng `state.json`). Để trống ô đó = chạy bình thường ở ngưỡng 0.63.

## Chỉnh sửa
- **Đổi ngưỡng:** sửa `ALERT_THRESHOLD` trong `.github/workflows/messi-monitor.yml`.
- **Đổi tần suất:** sửa dòng `cron:` (vd `*/5 * * * *` = mỗi 5 phút — mức nhỏ nhất GitHub cho phép).
- **Theo dõi cầu thủ khác:** đổi `PLAYER_KEYWORD` trong `messi_goldenboot_monitor.py`.

## Chi phí
- GitHub Actions: **miễn phí** (dùng rất ít phút).
- Resend: free tier **3.000 email/tháng** — thừa sức.
