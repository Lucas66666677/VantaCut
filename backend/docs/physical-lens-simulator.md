# 物理級鏡頭模擬器

`LensPhysicsProfile` 以 Cauchy 色散式 `n(λ)=A+B/λ²+C/λ⁴` 計算各波長折射率，並以薄透鏡近似 `fλ≈fref(nref−1)/(nλ−1)` 得到 Longitudinal Chromatic Aberration 的焦移與畫面徑向 R/B 位移。PSF 使用 Airy diffraction 項與可校準的球面像差包絡；MTF 由 `MTF(f)=|FFT(PSF)|` 計算並可輸出給鏡頭 chart 校正流程。

`frontend/lib/shaders/lens-psf-compute.ts` 使用 16×16 workgroup 與含 7px halo 的 30×30 workgroup shared-memory tile。每個 tile 協作從全域紋理讀取一次，再在 shared memory 執行 RGB 光譜位移後的 PSF 卷積；此設計適合 4K Proxy 預覽。最終量測級風格需以特定鏡頭、光圈、對焦距離與實拍 MTF chart 校正，不能僅憑「Nikon FM」名稱推定真實鏡頭處方。
