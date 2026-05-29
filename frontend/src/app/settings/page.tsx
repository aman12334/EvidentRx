"use client";
import { useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";

type Theme    = "system" | "light" | "dark";
type Density  = "compact" | "comfortable" | "spacious";

interface Preferences {
  theme:               Theme;
  density:             Density;
  emailAlerts:         boolean;
  escalationAlerts:    boolean;
  weeklyDigest:        boolean;
  defaultPageSize:     number;
  displayName:         string;
}

const DEFAULTS: Preferences = {
  theme:             "system",
  density:           "comfortable",
  emailAlerts:       true,
  escalationAlerts:  true,
  weeklyDigest:      false,
  defaultPageSize:   25,
  displayName:       "",
};

export default function SettingsPage() {
  const [prefs, setPrefs]   = useState<Preferences>(DEFAULTS);
  const [saved,  setSaved]  = useState(false);

  const update = <K extends keyof Preferences>(key: K, value: Preferences[K]) => {
    setPrefs((p) => ({ ...p, [key]: value }));
    setSaved(false);
  };

  const handleSave = () => {
    // Persist to localStorage; real apps would POST to /api/v1/users/me/preferences
    localStorage.setItem("evidentrx:preferences", JSON.stringify(prefs));
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Manage your account preferences and notification settings."
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Appearance */}
        <Card padding="md">
          <CardHeader><CardTitle>Appearance</CardTitle></CardHeader>
          <div className="mt-4 space-y-5">
            <Field label="Theme">
              <SegmentedControl
                options={[
                  { value: "system", label: "System" },
                  { value: "light",  label: "Light"  },
                  { value: "dark",   label: "Dark"   },
                ]}
                value={prefs.theme}
                onChange={(v) => update("theme", v as Theme)}
              />
            </Field>

            <Field label="Display density">
              <SegmentedControl
                options={[
                  { value: "compact",     label: "Compact"     },
                  { value: "comfortable", label: "Comfortable" },
                  { value: "spacious",    label: "Spacious"    },
                ]}
                value={prefs.density}
                onChange={(v) => update("density", v as Density)}
              />
            </Field>

            <Field label="Default page size">
              <select
                value={prefs.defaultPageSize}
                onChange={(e) => update("defaultPageSize", Number(e.target.value))}
                className="rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm text-slate-700 dark:text-slate-300 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
              >
                {[10, 25, 50, 100].map((n) => (
                  <option key={n} value={n}>{n} rows</option>
                ))}
              </select>
            </Field>
          </div>
        </Card>

        {/* Profile */}
        <Card padding="md">
          <CardHeader><CardTitle>Profile</CardTitle></CardHeader>
          <div className="mt-4 space-y-5">
            <Field label="Display name">
              <input
                type="text"
                value={prefs.displayName}
                onChange={(e) => update("displayName", e.target.value)}
                placeholder="Your name (shown in case notes)"
                className="w-full rounded-md border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 px-3 py-1.5 text-sm text-slate-800 dark:text-slate-200 placeholder:text-slate-400 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
              />
            </Field>
          </div>
        </Card>

        {/* Notifications */}
        <Card padding="md" className="lg:col-span-2">
          <CardHeader><CardTitle>Notifications</CardTitle></CardHeader>
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Toggle
              label="Email alerts"
              description="Receive email on new critical findings"
              checked={prefs.emailAlerts}
              onChange={(v) => update("emailAlerts", v)}
            />
            <Toggle
              label="Escalation alerts"
              description="Notify when cases are escalated"
              checked={prefs.escalationAlerts}
              onChange={(v) => update("escalationAlerts", v)}
            />
            <Toggle
              label="Weekly digest"
              description="Summary email every Monday morning"
              checked={prefs.weeklyDigest}
              onChange={(v) => update("weeklyDigest", v)}
            />
          </div>
        </Card>
      </div>

      {/* Save */}
      <div className="flex items-center gap-3">
        <Button onClick={handleSave} size="md">
          Save preferences
        </Button>
        {saved && (
          <p className="text-sm text-green-600 dark:text-green-400">
            ✓ Saved
          </p>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500 mb-1.5">
        {label}
      </label>
      {children}
    </div>
  );
}

interface SegmentOption { value: string; label: string }
function SegmentedControl({
  options, value, onChange,
}: {
  options: SegmentOption[];
  value:   string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-slate-200 dark:border-slate-700 overflow-hidden">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`px-3 py-1.5 text-xs font-medium transition-colors ${
            opt.value === value
              ? "bg-blue-600 text-white"
              : "bg-white dark:bg-slate-900 text-slate-600 dark:text-slate-400 hover:bg-slate-50 dark:hover:bg-slate-800"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function Toggle({
  label, description, checked, onChange,
}: {
  label:       string;
  description: string;
  checked:     boolean;
  onChange:    (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-md border border-slate-100 dark:border-slate-800 p-3 hover:bg-slate-50 dark:hover:bg-slate-800/50 transition-colors">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 accent-blue-600 cursor-pointer"
      />
      <div>
        <p className="text-sm font-medium text-slate-800 dark:text-slate-200">{label}</p>
        <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{description}</p>
      </div>
    </label>
  );
}
