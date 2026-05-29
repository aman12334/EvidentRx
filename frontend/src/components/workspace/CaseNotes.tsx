"use client";
import { useState } from "react";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button }     from "@/components/ui/Button";
import { Spinner }    from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

interface CaseNote {
  note_id:    string;
  case_id:    string;
  author:     string;
  body:       string;
  created_at: string;
}

async function fetchNotes(caseId: string): Promise<CaseNote[]> {
  const res = await fetch(`/api/v1/investigations/${caseId}/notes`);
  if (!res.ok) throw new Error("Failed to load notes");
  return res.json();
}

async function createNote(
  caseId: string,
  payload: { author: string; body: string }
): Promise<CaseNote> {
  const res = await fetch(`/api/v1/investigations/${caseId}/notes`, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(payload),
  });
  if (!res.ok) throw new Error("Failed to save note");
  return res.json();
}

interface CaseNotesProps {
  caseId:       string;
  currentUser?: string;
}

export function CaseNotes({ caseId, currentUser = "Analyst" }: CaseNotesProps) {
  const qc = useQueryClient();
  const [draft, setDraft]   = useState("");
  const [author, setAuthor] = useState(currentUser);

  const { data: notes = [], isLoading } = useQuery({
    queryKey: ["case-notes", caseId],
    queryFn:  () => fetchNotes(caseId),
  });

  const { mutate: addNote, isPending } = useMutation({
    mutationFn: (payload: { author: string; body: string }) =>
      createNote(caseId, payload),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["case-notes", caseId] });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    addNote({ author: author.trim() || "Analyst", body: draft.trim() });
  };

  return (
    <Card padding="md">
      <CardHeader>
        <CardTitle>Analyst Notes</CardTitle>
      </CardHeader>

      {/* Existing notes */}
      <div className="mt-4 space-y-3">
        {isLoading ? (
          <div className="flex justify-center py-8"><Spinner size="md" /></div>
        ) : notes.length === 0 ? (
          <EmptyState
            title="No notes yet"
            description="Add the first note to begin the analyst thread."
          />
        ) : (
          notes.map((note) => (
            <div
              key={note.note_id}
              className="rounded-md border border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/40 px-4 py-3"
            >
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <span className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                  {note.author}
                </span>
                <time className="text-xs text-slate-400">
                  {new Date(note.created_at).toLocaleString()}
                </time>
              </div>
              <p className="text-sm text-slate-700 dark:text-slate-300 whitespace-pre-wrap">
                {note.body}
              </p>
            </div>
          ))
        )}
      </div>

      {/* Compose new note */}
      <form onSubmit={handleSubmit} className="mt-5 space-y-3">
        <div className="flex gap-2">
          <div className="flex-1">
            <label className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1 block">
              Your name
            </label>
            <input
              type="text"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder="Analyst name"
              className="w-full rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm text-slate-800 dark:text-slate-200 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors"
            />
          </div>
        </div>

        <div>
          <label className="text-xs font-semibold uppercase tracking-wide text-slate-400 mb-1 block">
            Note
          </label>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add an analyst note…"
            rows={3}
            className="w-full resize-none rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-2 text-sm text-slate-800 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 transition-colors"
          />
        </div>

        <div className="flex justify-end">
          <Button
            type="submit"
            size="sm"
            loading={isPending}
            disabled={!draft.trim() || isPending}
          >
            Add Note
          </Button>
        </div>
      </form>
    </Card>
  );
}
