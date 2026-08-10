"use client";

export type LanguageReviewOverlayItem = {
  id: string;
  type: "grammar_correction" | "synonym_card";
  output_start: number;
  output_end: number;
  original_text?: string;
  correction?: string;
  explanation?: string;
  term?: string;
  synonyms?: Array<{ term: string; reason: string }>;
};

export function LanguageReviewOverlay({ currentTime, items }: { currentTime: number; items: LanguageReviewOverlayItem[] }) {
  const active = items.filter((item) => item.output_start <= currentTime && currentTime <= item.output_end);
  return <div className="pointer-events-none absolute inset-0 z-30 text-white">
    {active.filter((item) => item.type === "grammar_correction").map((item) => <div key={item.id} className="absolute left-1/2 top-[11%] w-[78%] -translate-x-1/2 rounded-2xl border-2 border-red-400/80 bg-zinc-950/85 px-6 py-4 text-center shadow-2xl">
      <div className="text-2xl font-bold text-red-400 line-through decoration-4">{item.original_text}</div>
      <div className="mt-1 text-2xl font-bold text-emerald-300">✓ {item.correction}</div>
      <div className="mt-2 text-sm text-zinc-200">{item.explanation}</div>
    </div>)}
    {active.filter((item) => item.type === "synonym_card").map((item) => <div key={item.id} className="absolute bottom-[12%] right-[8%] w-[34%] rounded-2xl border-2 border-violet-300/80 bg-violet-950/90 p-4 shadow-2xl">
      <div className="font-semibold text-violet-200">Advanced alternatives · {item.term}</div>
      {item.synonyms?.map((synonym) => <div key={synonym.term} className="mt-2"><span className="font-bold text-emerald-300">{synonym.term}</span><span className="ml-2 text-xs text-violet-100">{synonym.reason}</span></div>)}
    </div>)}
  </div>;
}
