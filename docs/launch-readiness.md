# VantaCut 上架檢查清單

這份清單將「程式能跑」與「可以對外開放」分開。只有全部完成，才能將流量導向正式環境。

> [!NOTE]
> 這份清單描述的是自架 Docker Compose + Nginx 拓樸。實際部署在 Render 免費方案上，
> 部署現況、已驗證項目與剩餘阻礙請看 [`docs/render-deployment.md`](./render-deployment.md)。

## 已在程式內完成

- Landing、Studio、互動範例、PWA manifest 與 404 體驗。
- 本機優先素材導入、背景上傳、雲端草稿與非破壞性 Timeline。
- API liveness (`/health`) 與 Database/Redis readiness (`/ready`) 探針。
- Docker production images、Nginx TLS proxy、安全標頭與 Render CI quality gate。
- Matrix export、SSE/WebSocket 任務狀態與媒體品質驗證。

## CI release preflight

`python scripts/release_preflight.py`（GitHub Actions workflow `Release preflight`）在每個 pull
request 上驗證發佈接線，只讀 repository，不需要任何 secret：

- `docker-compose*.yml` 沒有預設值的變數（含 `${VAR:?message}`）是否記載於對應的 env example。
- `.env.production.example` 是否設定所有會退回開發預設值的設定，`ENVIRONMENT=production`、
  `MOCK_AI=false` 是否成立，是否殘留 `minioadmin`／`localhost` 等開發值或看起來像真實憑證的值。
- production healthcheck 的探測二進位檔是否安裝在該 image、埠是否 `EXPOSE`、路徑是否為
  `backend/app/main.py` 宣告的 route；`/health` 與 `/ready` 是否都仍然存在。
- 所有 `condition: service_healthy` 的目標是否真的有 healthcheck；Nginx upstream 是否指向存在
  且有開埠的服務。
- `backend/migrations/` 的 revision 圖是否只有一個 head、沒有重複 revision id、沒有指向不存在的
  `down_revision`，且每個 migration 都能從 head 回溯到；`backend/alembic.ini` 的 `script_location`
  是否真的指向這些檔案（指錯時 `alembic upgrade head` 會「成功」但什麼都沒套用）。
- `backend/start.sh` 是否真的執行 `alembic upgrade head`，且在其之前就 `set -e`（否則 migration
  失敗仍會啟動 API，對外服務未升級的 schema）。
- `docker compose config` 是否通過（無 Docker 時標示 skipped，不會當成通過）。

它不驗證 secret 的實際內容、DNS、TLS 憑證與 managed service 可達性；那些仍屬下列部署主機步驟。

## 上線前必填設定

1. 以 `.env.production.example` 建立僅限部署平台讀取的 Secret；不可將任何 API key 放進 Git 或 `NEXT_PUBLIC_*`。
2. 設定 `CORS_ALLOWED_ORIGINS=https://你的正式網域`、`NEXT_PUBLIC_API_URL=https://你的正式網域`，以及真實的 S3、PostgreSQL、Redis 端點。
3. 將 TLS 憑證掛載到 `TLS_CERTS_PATH`，並將 Nginx `server_name` 改為正式網域。
4. 執行 `docker compose -f docker-compose.production.yml --env-file .env.production up -d --build`，接著執行 `docker compose -f docker-compose.production.yml exec api alembic upgrade head`。
5. 負載平衡器只將流量導到 `/ready` 回應 200 的 API；Worker 需獨立佈署到 `render` 與 GPU queue。

## 必須由營運／法務完成的外部條件

- ~~加入真正的登入／帳號驗證與 session cookie 策略~~：已完成。`/api/v1/auth/register`、`/login`、`/me` 已上線，`/studio` 由 `AuthGate` 擋住並顯示真實登入頁；176 個 API 路徑中有 170 個操作標註了 `security`。SSO 仍未實作。
- 配置 Stripe、YouTube、TikTok、OpenAI/Gemini/Suno 等正式 OAuth／API callback 網域與隱私權政策。
- 建立資料處理條款、AI 生成內容揭露、聲音／臉部處理同意書與刪除資料流程。
- 設定 S3 lifecycle、備份演練、Sentry/OTel 告警、WAF、速率限制與異常成本警報。

## 發布驗收

1. `GET /health` 與 `GET /ready` 均回傳 200。
2. 從瀏覽器上傳一支素材，確認直接上傳、預覽 Proxy、粗剪、字幕、單檔與 Matrix export 都能完成。
3. 由 CI 下載成品，確認 SSIM/PSNR、字幕位置與 A/V 延遲品質 gate 通過。
4. 用沒有權限的帳號、過期 presigned URL、損壞影片與 API 限流情境做負向測試。
