# Lecturas：AI 雙主持助教

`POST /api/v1/timelines/{timeline_id}/lecturas` 接收目前 Timeline、已授權的 `AvatarProfile` 與主素材。Worker 先將字幕時間戳與代理影片送給 `MultimodalProvider`，要求其回傳不超過四個、具理由與信心分數的提問／總結候選。

每段腳本使用中性 TTS，再以既有 Audio2Face + Unreal MRQ 產生帶 Alpha 的數位助教。系統建立新的、`is_current=false` 的 Timeline 版本，並把介入計畫寫入 `settings_json.lecturas`；原 Timeline 不會被改寫，使用者可先審閱再採用新版本。

- `presentation_mode=freeze`：在自然邊界插入主畫面凍結影格，助教由右側滑入，音軌切換為助教旁白；這會延長輸出時長。
- `presentation_mode=pip`：主講畫面縮至左上角、助教置於右下角，主音軌會 sidechain duck；此模式不改變輸出時長。
- 每段輸出資產和 Timeline 設定都標記 `AI teaching assistant / digital avatar`。不得將主講者的聲音、肖像或動作複製到助教；AvatarProfile 必須已有授權紀錄。
