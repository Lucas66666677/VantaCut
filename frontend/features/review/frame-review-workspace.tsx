"use client";

import { useMemo, useRef, useState } from "react";

import { ReviewAnnotationCanvas } from "@/features/review/review-annotation-canvas";
import type { FrameAnnotation, ReviewComment } from "@/types/review";

interface FrameReviewWorkspaceProps {
  videoSrc: string;
  fps: number;
  comments: ReviewComment[];
  onCreateComment: (input: { frameNumber: number; body: string; annotation: FrameAnnotation }) => Promise<void>;
  onResolveComment?: (comment: ReviewComment) => Promise<void>;
  /** Connect this to useVideoCanvasPlayer.seek(frame / fps * 1000) for frame-accurate proxy decoding. */
  onSeekFrame?: (frameNumber: number) => Promise<void>;
}

const blankAnnotation = (): FrameAnnotation => ({ canvas_width: 1280, canvas_height: 720, operations: [] });

export function FrameReviewWorkspace({ videoSrc, fps, comments, onCreateComment, onResolveComment, onSeekFrame }: FrameReviewWorkspaceProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [frameNumber, setFrameNumber] = useState(0);
  const [draftAnnotation, setDraftAnnotation] = useState<FrameAnnotation>(blankAnnotation);
  const [body, setBody] = useState("");
  const [selectedCommentId, setSelectedCommentId] = useState<string | null>(null);
  const selected = useMemo(() => comments.find((comment) => comment.id === selectedCommentId), [comments, selectedCommentId]);
  const shownAnnotation = selected?.annotation ?? draftAnnotation;

  const seekToFrame = async (targetFrame: number) => {
    if (onSeekFrame) {
      await onSeekFrame(targetFrame);
      setFrameNumber(targetFrame);
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    await new Promise<void>((resolve) => {
      const complete = () => { video.removeEventListener("seeked", complete); resolve(); };
      video.addEventListener("seeked", complete);
      video.currentTime = targetFrame / fps;
    });
    setFrameNumber(targetFrame);
  };

  return <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
    <div className="overflow-hidden rounded-xl border border-zinc-800 bg-black">
      <div className="relative aspect-video">
        <video ref={videoRef} src={videoSrc} controls className="h-full w-full" onPause={(event) => setFrameNumber(Math.round(event.currentTarget.currentTime * fps))} />
        <ReviewAnnotationCanvas annotation={shownAnnotation} editable={!selected} onChange={setDraftAnnotation} />
      </div>
      <div className="flex gap-2 border-t border-zinc-800 p-3">
        <input value={body} onChange={(event) => setBody(event.target.value)} placeholder={`在影格 ${frameNumber} 加入審閱意見`} className="min-w-0 flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm" />
        <button disabled={!body.trim()} onClick={async () => { await onCreateComment({ frameNumber, body, annotation: draftAnnotation }); setBody(""); setDraftAnnotation(blankAnnotation()); }} className="rounded bg-red-500 px-3 py-2 text-sm font-medium disabled:opacity-40">送出批註</button>
      </div>
    </div>
    <aside className="rounded-xl border border-zinc-800 bg-zinc-900 p-3">
      <h3 className="mb-3 text-sm font-semibold">審閱意見</h3>
      <div className="space-y-2">
        {comments.map((comment) => <article key={comment.id} className={`rounded-lg border p-3 ${selectedCommentId === comment.id ? "border-red-400 bg-red-500/10" : "border-zinc-700"}`}>
          <button className="w-full text-left" onClick={() => { setSelectedCommentId(comment.id); void seekToFrame(comment.frame_number); }}>
            <div className="flex justify-between text-xs"><span className="font-mono text-red-300">{comment.timecode}</span><span className="text-zinc-400">{comment.status}</span></div>
            <p className="mt-1 text-sm">{comment.body}</p><p className="mt-1 text-xs text-zinc-400">{comment.author_name}</p>
          </button>
          {comment.status === "open" && onResolveComment && <button onClick={() => void onResolveComment(comment)} className="mt-2 text-xs text-emerald-300">標示為已解決</button>}
        </article>)}
      </div>
    </aside>
  </section>;
}
