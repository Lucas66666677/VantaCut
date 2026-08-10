# 金融軌道

`POST /api/v1/timelines/{timeline_id}/finance-tracks` 建立非同步市場資料、技術指標與透明 K 線圖工作；`GET` 取得工作狀態與 OHLCV/指標；`PATCH` 更新創作者在 Canvas 畫出的貝茲支撐／壓力線並重新輸出圖層。

- `market=twse` 使用 TWSE 日資料，明確視為收盤資料而非即時報價。
- `market=yahoo_compatible` 只會呼叫設定好的 HTTPS、授權相容供應商；不使用未公開 Yahoo Finance 端點，且 API key 只留在 Worker 環境。
- Worker 以 OHLCV 計算 SMA20、SMA60、RSI14、MACD(12,26,9)。技術指標與圖表都帶有「僅供教學視覺化，非投資建議」資料聲明。
- 官方 TWSE 歷史資料會短暫快取於 Redis；第三方資料預設不快取，除非合約允許並設定 `FINANCE_YAHOO_CACHE_ALLOWED=true`。
- Render 階段下載 `finance-alpha.mov`，以 FFmpeg `overlay` 合成；貝茲線已燒在同一 RGBA 圖層，所以會與圖表一起受 Timeline 位置與運鏡影響。
