import React from 'react';
import { X, Sliders, RotateCw } from 'lucide-react';
import { Snippet } from '../types';

interface ExpandSnippetModalProps {
  expandSnippet: Snippet;
  preRoll: number;
  postRoll: number;
  isExpanding: boolean;
  expandError: string | null;
  expandSuccess: string | null;
  setPreRoll: (val: number) => void;
  setPostRoll: (val: number) => void;
  onClose: () => void;
  onSubmit: (e: React.FormEvent) => void;
}

export const ExpandSnippetModal: React.FC<ExpandSnippetModalProps> = ({
  expandSnippet,
  preRoll,
  postRoll,
  isExpanding,
  expandError,
  expandSuccess,
  setPreRoll,
  setPostRoll,
  onClose,
  onSubmit,
}) => {
  const isRetry = !expandSnippet.audioUrl || expandSnippet.extractionStatus === 'unavailable' || expandSnippet.duration <= 0;
  const totalDuration = preRoll + postRoll;

  const renderSubmitContent = () => {
    if (isExpanding) {
      return (
        <>
          <RotateCw className="w-3.5 h-3.5 animate-spin" />
          <span>Re-clipping & Transcribing...</span>
        </>
      );
    }
    if (isRetry) {
      return (
        <>
          <RotateCw className="w-3.5 h-3.5" />
          <span>Retry Extraction & Transcribe</span>
        </>
      );
    }
    return (
      <>
        <Sliders className="w-3.5 h-3.5" />
        <span>Update & Replace Snippet</span>
      </>
    );
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-[#0e0e0e] border border-neutral-700 w-full max-w-md p-5 space-y-4 font-mono shadow-2xl relative">
        {/* Modal Header */}
        <div className="flex items-start justify-between border-b border-neutral-800 pb-3">
          <div>
            <h3 className="text-sm font-semibold text-white uppercase tracking-tight flex items-center gap-2">
              {isRetry ? (
                <>
                  <RotateCw className="w-4 h-4 text-amber-400" />
                  <span>Retry Snipping & Extract Audio</span>
                </>
              ) : (
                <>
                  <Sliders className="w-4 h-4 text-neutral-300" />
                  <span>Expand Snippet Context</span>
                </>
              )}
            </h3>
            <p className="text-xs text-neutral-400 mt-1 truncate max-w-xs">
              {expandSnippet.bookTitle}
            </p>
            {isRetry && (
              <p className="text-[11px] text-amber-300/80 mt-1">
                Set pre-roll and post-roll timestamps around the bookmark anchor to retry extraction.
              </p>
            )}
          </div>
          <button
            onClick={() => !isExpanding && onClose()}
            className="text-neutral-400 hover:text-white p-1"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Adjustment Form */}
        <form onSubmit={onSubmit} className="space-y-4 text-xs">
          <div>
            <label
              htmlFor="pre-roll-number-input"
              aria-label="Pre-Roll duration before bookmark anchor in seconds"
              className="block text-neutral-300 mb-1.5"
            >
              Pre-Roll: <span className="text-white font-semibold">{preRoll}s</span> before bookmark anchor
            </label>
            <div className="flex items-center gap-3">
              <input
                id="pre-roll-slider"
                aria-label="Pre-Roll duration slider in seconds"
                type="range"
                min="5"
                max="180"
                step="5"
                value={preRoll}
                onChange={(e) => setPreRoll(Number(e.target.value))}
                className="w-full accent-neutral-200 cursor-pointer"
              />
              <input
                id="pre-roll-number-input"
                aria-label="Pre-Roll duration in seconds"
                type="number"
                min="5"
                max="300"
                value={preRoll}
                onChange={(e) => setPreRoll(Math.max(5, Number(e.target.value)))}
                className="w-16 bg-[#161616] border border-neutral-700 px-2 py-1 text-white text-center"
              />
            </div>
          </div>

          <div>
            <label
              htmlFor="post-roll-number-input"
              aria-label="Post-Roll duration after bookmark anchor in seconds"
              className="block text-neutral-300 mb-1.5"
            >
              Post-Roll: <span className="text-white font-semibold">{postRoll}s</span> after bookmark anchor
            </label>
            <div className="flex items-center gap-3">
              <input
                id="post-roll-slider"
                aria-label="Post-Roll duration slider in seconds"
                type="range"
                min="10"
                max="300"
                step="5"
                value={postRoll}
                onChange={(e) => setPostRoll(Number(e.target.value))}
                className="w-full accent-neutral-200 cursor-pointer"
              />
              <input
                id="post-roll-number-input"
                aria-label="Post-Roll duration in seconds"
                type="number"
                min="10"
                max="600"
                value={postRoll}
                onChange={(e) => setPostRoll(Math.max(10, Number(e.target.value)))}
                className="w-16 bg-[#161616] border border-neutral-700 px-2 py-1 text-white text-center"
              />
            </div>
          </div>

          {/* Total Calculation Display */}
          <div className="p-2.5 bg-[#141414] border border-neutral-800 flex items-center justify-between text-[11px]">
            <span className="text-neutral-400">Total New Snippet Duration:</span>
            <span className="text-white font-semibold font-mono">
              {totalDuration} seconds {totalDuration / 60 >= 1 ? `(${(totalDuration / 60).toFixed(1)} min)` : ''}
            </span>
          </div>

          {expandError && (
            <div className="p-2.5 bg-red-950/40 border border-red-800 text-red-300 text-xs">
              {expandError}
            </div>
          )}

          {expandSuccess && (
            <div className="p-2.5 bg-emerald-950/40 border border-emerald-800 text-emerald-300 text-xs">
              {expandSuccess}
            </div>
          )}

          {/* Action Buttons */}
          <div className="pt-3 border-t border-neutral-800 flex items-center justify-end gap-2.5">
            <button
              type="button"
              onClick={onClose}
              disabled={isExpanding}
              className="px-3 py-1.5 border border-neutral-700 hover:border-neutral-500 text-neutral-300 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isExpanding}
              className="px-4 py-1.5 bg-neutral-100 text-black hover:bg-white font-semibold flex items-center gap-2 transition-colors disabled:opacity-50"
            >
              {renderSubmitContent()}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
