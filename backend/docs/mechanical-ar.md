# 機械與電路追蹤

1. `POST /api/v1/timelines/{timeline_id}/mechanical-ar/code` 以 multipart 上傳 UTF-8 `.py` 或 Intel HEX `.hex`。程式只會經 AST/文字解析，絕不在 Worker 執行。
2. `POST /api/v1/timelines/{timeline_id}/mechanical-ar/analyze` 會以預先部署的 YOLO-World 權重，對固定的零樣本詞彙辨識杜邦線、馬達、感測器、齒輪、連桿、LEGO Spike Hub 等；再以 Farneback optical flow 找出齒輪轉向與機構移動。
3. 結果存於 `Timeline.settings_json.mechanical_ar`：包含元件觀測值、光流特徵、可人工審閱的 Timeline 效果，以及 Python 動作呼叫對應的程式碼行號。最終 Render 會以透明 WebM 疊加 AR 圖層。

安全與準確性：模型標籤是視覺假設；`illustrative_signal_flow` 是由可見線材與事件推論的教學動畫，**不是**電流、電壓或接線正確性的量測。Intel HEX 沒有原始行號／語意，故只記錄檔案結構，不產生自動程式行高亮。請在匯出前確認元件與連線。
