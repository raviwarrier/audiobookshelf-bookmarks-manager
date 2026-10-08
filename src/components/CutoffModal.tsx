import React, { useRef } from 'react';
import { X, Settings, AlertTriangle, Check, Clock, Calendar, RotateCw } from 'lucide-react';
import { SyncState } from '../types';

interface CutoffModalProps {
  isOpen: boolean;
  selectedCutoffMode: 'from_start' | 'custom_date' | 'from_now';
  customDateInput: string;
  cutoffSaveError: string | null;
  cutoffSaveSuccess: string | null;
  isSavingCutoff: boolean;
  syncState?: SyncState | null;
  setSelectedCutoffMode: (mode: 'from_start' | 'custom_date' | 'from_now') => void;
  setCustomDateInput: (val: string) => void;
  onClose: () => void;
  onSubmit: (e: React.FormEvent) => void;
}

export function formatToSlashDate(dateStr?: string | null): string {
  if (!dateStr) return '';
  return dateStr.slice(0, 10).replaceAll('-', '/').replaceAll('.', '/');
}

function formatThreePartDate(p1: string, p2: string, p3: string): string {
  const y = p1.length === 2 ? `20${p1}` : p1;
  return `${y}-${p2.padStart(2, '0')}-${p3.padStart(2, '0')}`;
}

export function normalizeToIsoDate(raw?: string | null): string {
  if (!raw) return '';
  const trimmed = raw.trim();
  let sep = '-';
  if (trimmed.includes('/')) {
    sep = '/';
  } else if (trimmed.includes('.')) {
    sep = '.';
  }
  const parts = trimmed.split(sep).map((p) => p.trim());
  if (parts.length !== 3) return trimmed;

  const [p1, p2, p3] = parts;
  if (p1.length >= 2 && p1.length <= 4) {
    return formatThreePartDate(p1, p2, p3);
  }
  if (p3.length >= 2 && p3.length <= 4) {
    return formatThreePartDate(p3, p1, p2);
  }
  return trimmed;
}

export const CutoffModal: React.FC<CutoffModalProps> = ({
  isOpen,
  selectedCutoffMode,
  customDateInput,
  cutoffSaveError,
  cutoffSaveSuccess,
  isSavingCutoff,
  syncState,
  setSelectedCutoffMode,
  setCustomDateInput,
  onClose,
  onSubmit,
}) => {
  const nativeDatePickerRef = useRef<HTMLInputElement>(null);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-4">
      <div className="bg-[#121212] border border-neutral-800 max-w-md w-full p-6 text-xs text-neutral-200 font-mono shadow-2xl relative">
        <div className="flex items-center justify-between border-b border-neutral-800 pb-3 mb-4">
          <div className="flex items-center gap-2">
            <Settings className="w-4 h-4 text-emerald-400" />
            <h3 className="font-semibold text-white uppercase text-sm">
              Bookmark Cutoff Period
            </h3>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-400 hover:text-white p-1"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-neutral-400 mb-4 text-[11px] leading-relaxed">
          Configure which historical Audiobookshelf bookmarks are processed. Only bookmarks created within this period will be transcribed and clipped.
        </p>

        {cutoffSaveError && (
          <div className="p-2.5 mb-3 bg-red-950/40 border border-red-800 text-red-400 flex items-center gap-2 text-[11px]">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>{cutoffSaveError}</span>
          </div>
        )}

        {cutoffSaveSuccess && (
          <div className="p-2.5 mb-3 bg-emerald-950/40 border border-emerald-800 text-emerald-300 flex items-center gap-2 text-[11px]">
            <Check className="w-3.5 h-3.5 shrink-0" />
            <span>{cutoffSaveSuccess}</span>
          </div>
        )}

        <form onSubmit={onSubmit} className="space-y-4">
          <div className="space-y-2.5">
            {/* Option 1: From Start */}
            <label
              htmlFor="cutoff-mode-from-start"
              aria-label="From start: Process all bookmarks from the beginning of the server"
              className={`flex items-start gap-3 p-3 border cursor-pointer transition-colors ${
                selectedCutoffMode === 'from_start'
                  ? 'border-neutral-200 bg-neutral-900/90 text-white'
                  : 'border-neutral-800 bg-[#161616] text-neutral-300 hover:border-neutral-700'
              }`}
            >
              <input
                id="cutoff-mode-from-start"
                type="radio"
                name="cutoff_mode"
                value="from_start"
                aria-label="From start: Process all bookmarks from the beginning of the server"
                checked={selectedCutoffMode === 'from_start'}
                onChange={() => setSelectedCutoffMode('from_start')}
                className="mt-0.5 text-neutral-100 focus:ring-0"
              />
              <div className="space-y-0.5">
                <div className="font-semibold flex items-center gap-1.5">
                  <Clock className="w-3 h-3 text-amber-400" />
                  <span>From start</span>
                </div>
                <div className="text-[11px] text-neutral-400">
                  Process all bookmarks from the beginning of the server.
                </div>
              </div>
            </label>

            {/* Option 2: From Custom Date (YYYY/MM/DD) */}
            <label
              htmlFor="cutoff-mode-custom-date"
              aria-label="From custom date: Only process bookmarks created on or after this specific date"
              className={`flex items-start gap-3 p-3 border cursor-pointer transition-colors ${
                selectedCutoffMode === 'custom_date'
                  ? 'border-neutral-200 bg-neutral-900/90 text-white'
                  : 'border-neutral-800 bg-[#161616] text-neutral-300 hover:border-neutral-700'
              }`}
            >
              <input
                id="cutoff-mode-custom-date"
                type="radio"
                name="cutoff_mode"
                value="custom_date"
                aria-label="From custom date: Only process bookmarks created on or after this specific date"
                checked={selectedCutoffMode === 'custom_date'}
                onChange={() => setSelectedCutoffMode('custom_date')}
                className="mt-0.5 text-neutral-100 focus:ring-0"
              />
              <div className="space-y-2 flex-1">
                <div className="font-semibold flex items-center gap-1.5">
                  <Calendar className="w-3 h-3 text-sky-400" />
                  <span>From yyyy/mm/dd</span>
                </div>
                <div className="text-[11px] text-neutral-400">
                  Only process bookmarks created on or after this specific date at 00:00.
                </div>

                {selectedCutoffMode === 'custom_date' && (
                  <div className="pt-1">
                    <div className="relative flex items-center">
                      <input
                        id="custom-date-text-input"
                        type="text"
                        value={customDateInput}
                        onChange={(e) => setCustomDateInput(e.target.value)}
                        placeholder="yyyy/mm/dd"
                        aria-label="Custom cutoff date in YYYY/MM/DD format"
                        className="w-full bg-[#1e1e1e] border border-neutral-700 focus:border-neutral-300 text-white px-2.5 py-1.5 text-xs font-mono outline-none pr-9 tracking-wider"
                      />
                      <input
                        id="custom-date-picker-input"
                        type="date"
                        ref={nativeDatePickerRef}
                        className="sr-only"
                        tabIndex={-1}
                        aria-label="Native calendar date picker"
                        value={normalizeToIsoDate(customDateInput)}
                        max={new Date().toISOString().slice(0, 10)}
                        onChange={(e) => {
                          if (e.target.value) {
                            setCustomDateInput(formatToSlashDate(e.target.value));
                          }
                        }}
                      />
                      <button
                        type="button"
                        onClick={() => {
                          try {
                            nativeDatePickerRef.current?.showPicker();
                          } catch {
                            nativeDatePickerRef.current?.focus();
                          }
                        }}
                        className="absolute right-2 p-1 text-neutral-400 hover:text-white transition-colors"
                        title="Open calendar picker"
                      >
                        <Calendar className="w-3.5 h-3.5" />
                      </button>
                    </div>
                    <div className="flex items-center justify-between text-[10px] text-neutral-400 mt-1 px-0.5">
                      <span>Format: <strong className="text-neutral-300 font-mono">yyyy/mm/dd</strong> or <strong className="text-neutral-300 font-mono">yy/mm/dd</strong></span>
                      <span className="text-neutral-500">Pick from calendar or type</span>
                    </div>
                  </div>
                )}
              </div>
            </label>

            {/* Option 3: From Now (Installation Date) */}
            <label
              htmlFor="cutoff-mode-from-now"
              aria-label="From now: Process bookmarks created on or after installation date"
              className={`flex items-start gap-3 p-3 border cursor-pointer transition-colors ${
                selectedCutoffMode === 'from_now'
                  ? 'border-neutral-200 bg-neutral-900/90 text-white'
                  : 'border-neutral-800 bg-[#161616] text-neutral-300 hover:border-neutral-700'
              }`}
            >
              <input
                id="cutoff-mode-from-now"
                type="radio"
                name="cutoff_mode"
                value="from_now"
                aria-label="From now: Only process bookmarks created on or after installation date"
                checked={selectedCutoffMode === 'from_now'}
                onChange={() => setSelectedCutoffMode('from_now')}
                className="mt-0.5 text-neutral-100 focus:ring-0"
              />
              <div className="space-y-0.5">
                <div className="font-semibold flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>
                  <span>From now (installation date)</span>
                </div>
                <div className="text-[11px] text-neutral-400">
                  Process bookmarks created on or after installation date ({syncState?.installation_date || 'first install'}). Older historical bookmarks are excluded.
                </div>
              </div>
            </label>
          </div>

          {/* Modal Actions */}
          <div className="pt-3 border-t border-neutral-800 flex items-center justify-end gap-2.5">
            <button
              type="button"
              onClick={onClose}
              disabled={isSavingCutoff}
              className="px-3 py-1.5 border border-neutral-700 hover:border-neutral-500 text-neutral-300 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSavingCutoff}
              className="px-4 py-1.5 bg-neutral-100 text-black hover:bg-white font-semibold flex items-center gap-2 transition-colors disabled:opacity-50"
            >
              {isSavingCutoff ? (
                <>
                  <RotateCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Saving...</span>
                </>
              ) : (
                <span>Save & Apply</span>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
