# 學術特化自動組裝

`POST /api/v1/timelines/{timeline_id}/academic-mode` 設定 Academic Glossary、目標學程與保守語速，並建立一個可審閱的 Timeline 子版本。Narrative planner 固定輸出 Motivation → Methodology → Results → Future Works；其目標比重分別為 20-25%、35-40%、25-30%、10-15%，但會保留缺少結果或證據時的風險提示，而非虛構學術成果。

Glossary 採 canonical term + aliases：精確命中會校正 SRT/ASS 與未來 ASR／粗剪逐字稿；疑似音近字只會列入 `academic_glossary_review`，不會自動改字。這能顯著降低 `Photonics`、`Multimodal NN` 等術語錯誤，但 ASR 不可能承諾 100% 準確，提交前必須人工覆核。

Academic LUT 是低飽和、輕度提升對比的中性冷調 `.cube`。導出時可套用全片同步的 0.90-1.03 `atempo`/`setpts`、EQ 與輕度壓縮，維持音畫同步；其目的僅是降低生活化 VLOG 風格，並不測量或保證「自信度」、錄取率或學術能力。
