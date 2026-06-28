import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2, Circle, Loader2, PauseCircle, PlayCircle,
  Plus, Target, Trash2, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  addUloObjective, deleteUloObjective, getUloObjectives,
  pauseUloObjective, resumeUloObjective,
  type UloObjective,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

// ── Helpers ───────────────────────────────────────────────────────────────

const PRIO_COLORS: Record<string, string> = {
  high:   "bg-red-500/15 text-red-600 dark:text-red-400",
  medium: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  low:    "bg-slate-500/15 text-slate-600 dark:text-slate-400",
};

function PrioBadge({ priority }: { priority: string }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${PRIO_COLORS[priority] ?? PRIO_COLORS.low}`}>
      {priority}
    </span>
  );
}

function ComplianceBar({ rate }: { rate: number | null }) {
  if (rate === null) return <span className="text-xs text-muted-foreground">no data yet</span>;
  const pct = Math.round(rate * 100);
  const color = pct >= 75 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-24 rounded-full bg-muted">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-muted-foreground">{pct}%</span>
    </div>
  );
}

// ── Objective card ────────────────────────────────────────────────────────

interface ObjectiveCardProps {
  obj: UloObjective;
  channel: string;
  chatKey: string;
  csrf: string;
  onChanged: () => void;
}

function ObjectiveCard({ obj, channel, chatKey, csrf, onChanged }: ObjectiveCardProps) {
  const [confirmDelete, setConfirmDelete] = React.useState(false);

  const pauseMut = useMutation({
    mutationFn: () =>
      obj.active
        ? pauseUloObjective(obj.id, channel, chatKey, csrf)
        : resumeUloObjective(obj.id, channel, chatKey, csrf),
    onSuccess: onChanged,
  });

  const deleteMut = useMutation({
    mutationFn: () => deleteUloObjective(obj.id, channel, chatKey, csrf),
    onSuccess: () => { setConfirmDelete(false); onChanged(); },
  });

  return (
    <Card className={obj.active ? "" : "opacity-60"}>
      <CardContent className="flex items-start gap-3 p-4">
        <div className="mt-0.5">
          {obj.active
            ? <CheckCircle2 className="h-4 w-4 text-emerald-500" />
            : <Circle className="h-4 w-4 text-muted-foreground" />}
        </div>
        <div className="flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{obj.text}</span>
            <PrioBadge priority={obj.priority} />
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span>id: <code className="font-mono">{obj.id}</code></span>
            {obj.turns_checked > 0 && (
              <span>{obj.turns_checked} checks</span>
            )}
            <ComplianceBar rate={obj.compliance_rate} />
          </div>
        </div>
        <div className="flex gap-1">
          <Button
            variant="ghost" size="icon"
            onClick={() => pauseMut.mutate()}
            disabled={pauseMut.isPending}
            title={obj.active ? "Pause" : "Resume"}
          >
            {pauseMut.isPending
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : obj.active
              ? <PauseCircle className="h-4 w-4" />
              : <PlayCircle className="h-4 w-4" />}
          </Button>
          <Button
            variant="ghost" size="icon"
            onClick={() => setConfirmDelete(true)}
            title="Delete"
          >
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
      </CardContent>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete objective?</DialogTitle>
            <DialogDescription>
              This removes the objective permanently. Compliance history is lost.
            </DialogDescription>
          </DialogHeader>
          <p className="rounded border bg-muted px-3 py-2 text-sm italic">{obj.text}</p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>Cancel</Button>
            <Button
              variant="destructive"
              onClick={() => deleteMut.mutate()}
              disabled={deleteMut.isPending}
            >
              {deleteMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// ── Add dialog ────────────────────────────────────────────────────────────

interface AddDialogProps {
  open: boolean;
  onClose: () => void;
  channel: string;
  chatKey: string;
  csrf: string;
  onAdded: () => void;
}

function AddDialog({ open, onClose, channel, chatKey, csrf, onAdded }: AddDialogProps) {
  const [text, setText] = React.useState("");
  const [priority, setPriority] = React.useState<"low" | "medium" | "high">("medium");

  const mut = useMutation({
    mutationFn: () => addUloObjective(channel, chatKey, text.trim(), priority, csrf),
    onSuccess: () => {
      setText(""); setPriority("medium");
      onAdded(); onClose();
    },
  });

  const charsLeft = 200 - text.length;

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add Learning Objective</DialogTitle>
          <DialogDescription>
            Describe a behavioural constraint you want the AI to follow in this chat.
            Keep it under 200 characters.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Textarea
            placeholder="e.g. Always reply in German."
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="min-h-[80px] resize-none"
            maxLength={200}
          />
          <div className="flex items-center justify-between">
            <span className={`text-xs ${charsLeft < 20 ? "text-destructive" : "text-muted-foreground"}`}>
              {charsLeft} chars left
            </span>
            <Select
              value={priority}
              onChange={(e) => setPriority(e.target.value as typeof priority)}
              className="w-32"
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </Select>
          </div>
          {mut.isError && (
            <p className="text-sm text-destructive">
              {(mut.error as Error)?.message ?? "Failed to add objective"}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={() => mut.mutate()}
            disabled={!text.trim() || mut.isPending}
          >
            {mut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────

export function LearningObjectivesPage() {
  const { session } = useAuth();
  const qc = useQueryClient();

  // Default: Discord bridge, empty chat key as placeholder.
  // In a real integration, these come from the active chat session.
  const [channel, setChannel] = React.useState("discord");
  const [chatKey, setChatKey]  = React.useState("");
  const [addOpen, setAddOpen]  = React.useState(false);

  const q = useQuery({
    queryKey: ["ulo", channel, chatKey],
    queryFn: ({ signal }) => getUloObjectives(channel, chatKey, signal),
    enabled: !!chatKey,
    refetchInterval: 30_000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["ulo", channel, chatKey] });

  const csrf = session?.csrf_token ?? "";
  const objectives = q.data?.objectives ?? [];
  const active = objectives.filter((o) => o.active);
  const paused = objectives.filter((o) => !o.active);

  return (
    <div className="mx-auto max-w-2xl space-y-6 px-4 py-8">
      <div className="flex items-center gap-3">
        <Target className="h-6 w-6 text-primary" />
        <div>
          <h1 className="text-xl font-semibold">Learning Objectives</h1>
          <p className="text-sm text-muted-foreground">
            Behavioural constraints injected into every AI turn for this chat.
          </p>
        </div>
      </div>

      {/* Chat selector */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Chat context</CardTitle>
          <CardDescription className="text-xs">
            Objectives are scoped per chat. Enter the channel and chat key to load.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex gap-2">
          <Input
            placeholder="channel (e.g. discord)"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            className="w-36"
          />
          <Input
            placeholder="chat key"
            value={chatKey}
            onChange={(e) => setChatKey(e.target.value)}
            className="flex-1"
          />
        </CardContent>
      </Card>

      {/* Loading */}
      {q.isLoading && chatKey && (
        <div className="space-y-2">
          {[0, 1].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
        </div>
      )}

      {/* Error */}
      {q.isError && (
        <div className="flex items-center gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <XCircle className="h-4 w-4 shrink-0" />
          Failed to load objectives.
        </div>
      )}

      {/* Active objectives */}
      {chatKey && !q.isLoading && q.data && (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">
              Active{" "}
              <Badge variant="secondary">{active.length}</Badge>
            </h2>
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <Plus className="mr-1 h-4 w-4" /> Add objective
            </Button>
          </div>

          {active.length === 0 ? (
            <p className="rounded-md border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
              No active objectives — click "Add objective" to get started.
            </p>
          ) : (
            <div className="space-y-2">
              {active.map((o) => (
                <ObjectiveCard
                  key={o.id} obj={o}
                  channel={channel} chatKey={chatKey} csrf={csrf}
                  onChanged={invalidate}
                />
              ))}
            </div>
          )}

          {/* Paused objectives */}
          {paused.length > 0 && (
            <>
              <h2 className="text-sm font-medium text-muted-foreground">
                Paused{" "}
                <Badge variant="outline">{paused.length}</Badge>
              </h2>
              <div className="space-y-2">
                {paused.map((o) => (
                  <ObjectiveCard
                    key={o.id} obj={o}
                    channel={channel} chatKey={chatKey} csrf={csrf}
                    onChanged={invalidate}
                  />
                ))}
              </div>
            </>
          )}
        </>
      )}

      {/* Prompt when no chat key */}
      {!chatKey && (
        <p className="text-center text-sm text-muted-foreground">
          Enter a chat key above to load objectives.
        </p>
      )}

      <AddDialog
        open={addOpen} onClose={() => setAddOpen(false)}
        channel={channel} chatKey={chatKey} csrf={csrf}
        onAdded={invalidate}
      />
    </div>
  );
}
