"use client";

import { useState } from "react";

const wheels = ["Lift", "Gamma", "Gain"] as const;

export function ColorWheelsWorkspaceModule() {
  const [values, setValues] = useState<Record<(typeof wheels)[number], number>>({ Lift: 0, Gamma: 0, Gain: 0 });
  return (
    <section className="rounded-2xl border border-cyan-400/20 bg-zinc-900 p-4 shadow-xl">
      <h2 className="text-sm font-semibold text-zinc-100">Primary Color Wheels</h2>
      <p className="mt-1 text-xs text-zinc-500">調整值會保留在工作區；串接 Color Pipeline 後可提交為 LUT／節點參數。</p>
      <div className="mt-4 grid grid-cols-3 gap-3">
        {wheels.map((wheel) => (
          <label key={wheel} className="text-center text-xs text-zinc-300">
            <span className="mx-auto mb-2 block h-16 w-16 rounded-full border-4 border-cyan-400/60 bg-[conic-gradient(#f43f5e,#facc15,#22c55e,#38bdf8,#a855f7,#f43f5e)] shadow-inner" />
            {wheel} {values[wheel] > 0 ? "+" : ""}{values[wheel]}
            <input className="mt-2 w-full accent-cyan-400" type="range" min="-100" max="100" value={values[wheel]} onChange={(event) => setValues({ ...values, [wheel]: Number(event.target.value) })} />
          </label>
        ))}
      </div>
    </section>
  );
}
